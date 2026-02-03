# Lattice Refactoring Plan

8 phases, ordered to minimize breakage. Run `pytest` after each phase.

Execution order: 1 → 2 → 6 → 7 → 5 → 3 → 4 → 8

---

## Phase 1: Extract Shared Command Validation

**Problem**: Command validation is duplicated between `router_main.py:486-537` and `execution_modes.py:165-208` with near-identical `blocked_tokens`, `default_allow`, and `_command_is_dangerous` logic. Windows patterns (`rd /s /q`, `Remove-Item -Recurse -Force`, `cmd /c rd`) are missing.

**Changes**:

- **Create `src/lattice/command_validation.py`**: Single source of truth containing:
  - `DANGEROUS_PATTERNS` list — existing 12 regex patterns + new Windows patterns:
    - `r"\brd\s+/s\s+/q\b"`
    - `r"\bremove-item\b.*-recurse.*-force"` (case-insensitive)
    - `r"\bcmd\s+/c\s+rd\b"`
    - `r"\bcmd\s+/c\s+del\b"`
    - `r"\brimraf\b\s+[/\\]"`
  - `BLOCKED_NETWORK_TOKENS` list (curl, wget, iwr, ssh, etc.)
  - `DEFAULT_SAFE_COMMANDS` frozenset (python, pip, npm, git, etc.)
  - `command_is_dangerous(cmd) -> bool`
  - `validate_command(cmd, *, allowlist, denylist) -> Optional[str]` — returns None if allowed, reason string if blocked

- **Modify `src/lattice/router_main.py`**: Replace `_command_is_dangerous()` (~line 401) and `_validate_command()` (~line 486) with thin wrappers calling `command_validation.validate_command(...)`. Keep method signatures identical.

- **Modify `src/lattice/execution_modes.py`**: Replace `_command_is_dangerous()` (line 146) and `_validate_command()` (line 165) with same thin wrappers.

- **Update `tests/test_command_validation.py`**: Add assertions for `rd /s /q`, `Remove-Item -Recurse -Force`, `cmd /c rd`. Existing tests continue passing since interfaces unchanged.

---

## Phase 2: Rename `semantic_search` → `rag_search`

**Problem**: Tool exposed to agent LLMs as "semantic_search" but it's BM25 keyword matching. Misleading name.

**Changes** (exhaustive grep of all call sites):

| File | What to change |
|------|---------------|
| `src/lattice/agent_tools.py:98` | Tool schema name `"semantic_search"` → `"rag_search"` |
| `src/lattice/agent_tools.py:344-349` | Dispatch branch and `rag.search_semantic()` call → `rag.search()` |
| `src/lattice/router/tools.py:269` | Tool schema name → `"rag_search"` |
| `src/lattice/router_main.py:~2398` | `elif tool_name == "semantic_search":` → `"rag_search"` |
| `src/lattice/rag.py:415` | Rename `search_semantic` → `search_rag`, keep `search_semantic` as deprecated alias |
| `src/lattice/agents.py:~193,361-364` | Update any `_rag_search_semantic` references |

- Update tool description to: `"Keyword-based search (BM25) over run-scoped artifacts and transcripts."`
- Add deprecated alias in `rag.py`: `search_semantic = search_rag`

---

## Phase 6: Dependency Hygiene

**Problem**: `pyproject.toml` and `requirements.txt` disagree. `jinja2` missing from pyproject, `trafilatura`/`PyYAML`/`starlette`/`pytest-cov` missing from requirements.txt.

**Changes**:

- **`pyproject.toml`**: Add `jinja2>=3.1.0` to `dependencies`
- **`requirements.txt`**: Regenerate to match pyproject.toml. Add header comment noting it should stay in sync with pyproject.toml. Include all core + test deps.

---

## Phase 7: Replace `sitecustomize.py`

**Problem**: `sitecustomize.py` at repo root auto-sets `PYTEST_DISABLE_PLUGIN_AUTOLOAD` but only works if it's on `sys.path`, which is fragile.

**Changes**:

- **`tests/conftest.py`**: Add `os.environ.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")` at the top, before other imports. conftest.py is always loaded by pytest — more reliable.
- **Delete `sitecustomize.py`** from repo root.

---

## Phase 5: OpenAPI Validation Enhancement

**Problem**: `contracts.py:42` `_validate_openapi_rough` is pure regex heuristic — checks for presence of `openapi`, `paths`, `components` keywords but doesn't validate schema structure.

**Changes**:

- **`src/lattice/contracts.py`**: Add `_validate_openapi(text) -> Dict` wrapper:
  - Try `import openapi_spec_validator` + `yaml.safe_load` → real validation
  - On `ImportError`: fall back to `_validate_openapi_rough`
  - On validation error: return rough result + `validation_errors` list
  - Add `"method": "openapi-spec-validator"` or `"regex"` to result dict
- **Update call site** in `ContractRunner.run_test()` (~line 410): call `_validate_openapi` instead of `_validate_openapi_rough`
- **`pyproject.toml`**: Add optional dependency group:
  ```toml
  [project.optional-dependencies]
  validation = ["openapi-spec-validator>=0.7.0"]
  ```
- **Add `tests/test_openapi_validation.py`**: Test both paths (monkeypatch the import for fallback case).

---

## Phase 3: Remove Hardcoded Agent Scaffolds

**Problem**: `FrontendAgent.act()` (lines 423-855) emits ~430 lines of hardcoded NovaFlow HTML/CSS/JS. `BackendAgent.act()` (lines 933-1099) emits ~165 lines of hardcoded Python HTTP server. `TestAgent.act()` (lines ~1216+) emits a hardcoded smoke test. None of this adapts to the user's goal.

**Changes**:

- **`src/lattice/agents.py` — `BaseAgent`**: Add shared helper `_parse_and_write_fenced_files(text, default_dir) -> List[ArtifactRef]` that extracts `` ```file:path `` `` `` fenced blocks from LLM output and writes each via `_write_artifact()`.

- **`FrontendAgent.act()`**: Delete hardcoded HTML/CSS/JS scaffold (~430 lines). Replace with:
  - System prompt instructing the LLM to generate frontend files (index.html, styles.css, app.js) using `` ```file:path `` `` `` fences
  - User prompt containing goal + decisions + huddle context + checklist
  - `_run_with_tools(messages)` call
  - `_parse_and_write_fenced_files(output, "frontend")` to extract and write files
  - Keep wireframes mode path (lines 857-890) as-is since it's already LLM-driven

- **`BackendAgent.act()`**: Delete hardcoded Python server scaffold (~165 lines). Same pattern — LLM generates backend files.

- **`TestAgent.act()`**: Delete hardcoded smoke test. LLM generates test files + contract specs. Extract JSON block for `contracts/tests/contract_tests.json`.

- **`FrontendAgent.plan()` / `BackendAgent.plan()`**: Keep keyword-based mode selection but update descriptions to be generic (remove "contact form" references).

- **Update `tests/test_integration_ladder_mode.py`**: Expand stub HTTP server enqueue calls. Since agents now make LLM calls instead of emitting hardcoded output, each agent's act() will hit the stub server. Enqueue responses containing `write_file` tool calls for each expected artifact (frontend/index.html, backend/app.py, tests/smoke_post_contact.py).

---

## Phase 4: Router Mode Delegation

**Problem**: `router_main.py` has inline ladder/tracks/weave logic in `_run_classic()` (~lines 937-1100) that duplicates what `execution_modes.py` already abstracts. The router should delegate step sequencing to mode classes.

**Changes**:

- **`src/lattice/router_main.py` — `_run_classic()`**: Replace inline mode branching with:
  ```python
  mode_handler = ExecutionModeFactory.create(
      self.mode, self.run_dir, self.logger, self.artifacts, self.rag, self.cfg
  )
  mode_handler.workspace_root = self.cwd
  result = mode_handler.execute(goal, transcript)
  ```
  Remove the ~150 lines of inline ladder/tracks/weave step logic that this replaces.

- **`src/lattice/execution_modes.py`**: Add a `step(step_name, goal, transcript)` method to `ExecutionMode` that the agentic loop (`_run_agentic`) can call for individual stages. This lets the LLM-driven router call mode steps one at a time while the structured path calls `execute()` for the full sequence.

- **Deprecate `src/lattice/router_simplified.py`**: Add `warnings.warn("SimplifiedRouter is deprecated; use RouterRunner", DeprecationWarning, stacklevel=2)` at import time. Keep file for backward compat, remove in a future release.

---

## Phase 8: Integration Test for Router Delegation

**Problem**: Integration test exercises `ExecutionModeFactory` directly, not the `RouterRunner → mode` delegation path.

**Changes**:

- **`tests/test_integration_ladder_mode.py`**: Add `test_ladder_mode_via_router_runner()`:
  - Same stub HTTP server setup
  - Instantiate `RouterRunner(cwd=str(tmp_path), mode="ladder")`
  - Call `runner.run("Build a minimal contact app")`
  - Assert same outputs (workspace files, gate results, JSONL events)
  - Verify `fallback_chain == ["openai"]` in model call events

- Keep existing `test_ladder_mode_end_to_end_hits_http_and_tools` as a unit test of the mode classes directly.

---

## Verification

After all phases:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest -q
```

Expected: all tests pass (97 existing + new tests for command validation, OpenAPI validation, rag_search rename, router delegation).

Additional manual checks:
- `grep -r "semantic_search" src/` returns only the deprecated alias in `rag.py`
- `grep -r "NovaFlow" src/` returns zero results
- `python -c "from lattice.command_validation import validate_command; assert validate_command('rd /s /q C:\\') is not None"`
- `pip install -e ".[test]"` succeeds
