LATTICE — Router Action Loop with Tools, Weave Mode, Provenance, and Finalization (M4.5)

LATTICE is a CLI‑only, single‑process orchestration runtime. It coordinates multiple agents via a deterministic Router, runs contract tests and stage gates, captures provenance, supports mid‑flight replanning, and emits a rigorous finalization report and deliverables.

Highlights (Milestone 4.5)
- LLM‑driven Router action loop: the Router LLM chooses modes, opens huddles, records DecisionSummaries, spawns/schedules sub‑agents, runs RAG/web search (if available), executes contract tests, and finalizes.
- Router Tools exposed as function calls: set_mode, open_huddle, record_decision_summary, inject_summary, spawn_agents, schedule_slice, rag_search, web_search (Groq or adapter), run_contract_tests, propose_advance_step, write_artifact, read_artifact, finalize_run.
- Host‑enforced safety: stage gates are authoritative (advance only via propose_advance_step), artifacts/logs persisted immutably under artifacts/, summaries injected compactly, and all actions/observations are logged.
- Weave mode retained: Hybrid critical path + docs track, and mid‑flight replanning via knowledge signals and huddles.
- Provenance & citations retained from M4 and consolidated during finalization.


Getting Started

- Requirements: Python 3.9+, `requests` (installed via setup), provider credentials if using remote models.
- Install:
  - pip: `pip install -e .`
  - or module: run via `python -m src.lattice.cli ...` from repo root.

CLI Commands

- lattice run "<goal>"
  - Default: Router runs the LLM‑driven action loop (M4.5). The LLM selects a mode (often weave for README/docs), spawns agents, schedules slices, opens huddles and records DecisionSummaries, queries RAG/web, requests gate advances, and calls finalize_run.
  - Emits `runs/<run_id>/artifacts/*`, structured logs `run.jsonl`, PlanGraph snapshot, DecisionSummaries, knowledge journal, and a finalization report with a deliverables zip.

- lattice logs <run_id> [--follow] [--output-only|-O]
  - Prints the JSONL log or formatted model outputs.

- lattice scrub [<run_id>]
  - Redacts secrets from `config.json` and `run.jsonl` for the run or for all runs.


Providers & Environment

- Default provider order
  - Router: Groq → LM Studio fallback
  - Agents: Gemini (OpenAI‑compatible surface) → LM Studio fallback

- Credentials
  - Router: `export GROQ_API_KEY=...`
  - Agents: `export GEMINI_API_KEY=...`

- Optional overrides
  - Force provider: `LATTICE_PROVIDER=groq` (applies to router+agents), or `LATTICE_ROUTER_PROVIDER`, `LATTICE_AGENT_PROVIDER`
  - Provider order: `LATTICE_ROUTER_PROVIDER_ORDER=groq,lmstudio`, `LATTICE_AGENT_PROVIDER_ORDER=gemini-openai-compat,lmstudio`
  - Default model (first provider): `LATTICE_ROUTER_MODEL`, `LATTICE_AGENT_MODEL`
  - Temperature/tokens: `LATTICE_TEMPERATURE`, `LATTICE_MAX_TOKENS`
  - Huddles mode: `LATTICE_HUDDLES=dialog|synthesis`
  - Initial mode: `LATTICE_MODE=ladder|tracks|weave`
  - Router policy (M4 compatibility): `LATTICE_ROUTER_POLICY=llm|policy` (default: `llm`). Set `policy` to revert to deterministic M4 router.
  - Router action budget: `LATTICE_ROUTER_MAX_STEPS=32`
  - Web search tool exposure (Router): `LATTICE_WEB_SEARCH=auto|on|off` (default: auto; enabled when Groq is in the Router chain or an adapter URL is set). Optional adapter: `LATTICE_WEB_SEARCH_ADAPTER_URL=...`


Key Concepts

- Router modes & action loop
  - Ladder: strictly ordered critical path.
  - Tracks: parallel slices across agents.
  - Weave (M4): Ladder on the critical path, with a Docs/README track in parallel. A minimal PlanGraph is saved to `artifacts/plans/plan_graph.json` with nodes, edges, segment modes, and `reasons[]` (switch/replan causes).
  - Router LLM (M4.5): Operates an agentic loop. Each LLM turn issues exactly one tool call; the host executes it and returns an observation. The loop ends when the LLM calls `finalize_run` or the safety budget is reached.

- Router Tools (LLM‑visible)
  - set_mode, open_huddle, record_decision_summary, inject_summary, spawn_agents, schedule_slice, rag_search, web_search (Groq/browser_search or adapter), run_contract_tests, propose_advance_step, write_artifact, read_artifact, finalize_run.
  - Stage gates are authoritative: use `propose_advance_step` to move forward. The host evaluates gates and blocks when failing.

- Huddles → DecisionSummaries
  - The Router convenes agents. A Router LLM synthesizes 1–3 DecisionSummary JSON objects with topic, options, decision, rationale, risks, actions, contracts, links.
  - M4 extends DecisionSummary with optional `sources[]` (provenance) capturing `EvidenceRef` (artifact|rag_doc).

- Stage Gates & Contract Tests
  - Gates are boolean expressions such as `tests.pass('api_contract') and artifact.exists('backend/**')`.
  - Evaluations log `gate_eval` with `checked_conditions[]` and `evidence[]` (artifact/RAG provenance supporting pass/fail).
  - Contract tests are stored under `artifacts/contracts/tests/*.json`; results under `artifacts/contracts/results/*.json`.

- Knowledge Signal (no network)
  - Simulate “new info arrived” with a local drop‑in file under `artifacts/knowledge/*.json`:
    `{ "source":"artifact|rag_doc", "refs": [ { "type":"artifact", "id":"artifacts/...", "hash":"sha256:..." } ] }`
  - The Router ingests these, logs `knowledge_signal`, opens a huddle, and replans. In M4, such references may be attached to downstream DecisionSummaries’ `sources[]`.

- Finalization Pass
  - Emits `artifacts/finalization/report.json` with:
    - `linters`: placeholder entries (M4 no linters configured)
    - `tests`: parsed contract test results
    - `drift`: `contract_drift|spec_drift|evidence_drift` (compares current artifact hashes vs earlier EvidenceRef hashes)
    - `deliverables`: `artifacts/deliverables/deliverables.zip`
    - `decision_log_path`: `artifacts/decisions/decision_log.md`
    - `citation_index_path`: `artifacts/citations/index.json` (maps `ds_id → EvidenceRef[]`)


Example Workflow

1) Run a small CLI app with README
   - `lattice run "ship a small CLI + README"`
   - The Router LLM usually selects weave, spawns agents, schedules slices, opens a huddle to pin API and records DecisionSummaries (with citations), runs contract tests, requests gate advances, and calls finalize_run.

2) Simulate a knowledge update (optional)
   - Write a drop‑in under `runs/<run_id>/artifacts/knowledge/new_info.json` with `source` and `refs[]`.
   - Router logs `knowledge_signal`, opens a huddle, and replans (may remain in Weave or switch).

3) Inspect outputs
   - Scaffolds: `artifacts/backend/*`, `artifacts/frontend/*`
   - Contracts & tests: `artifacts/contracts/*`
   - Huddles & decisions: `artifacts/huddles/*`, `artifacts/decisions/*`
   - PlanGraph & snapshots: `artifacts/plans/*`
   - Knowledge journal: `artifacts/knowledge/*`
   - Finalization: `artifacts/finalization/report.json`, `artifacts/decisions/decision_log.md`, `artifacts/citations/index.json`, and `artifacts/deliverables/deliverables.zip`

4) Logs & scrubbing
   - `lattice logs <run_id> --output-only`
   - `lattice scrub <run_id>` or `lattice scrub` (all runs)


Provenance & Citations (M4 plumbing)

- EvidenceRef union:
  - Artifact: `{ "type":"artifact", "id":"artifacts/...", "hash":"sha256:..." }`
  - RAG doc: `{ "type":"rag_doc", "id":"doc-id", "score":0.42, "hash?":"..." }`
  - External (reserved for M5)
- Finalization emits a Decision Log with inline citations and a machine‑readable Citation Index.


Troubleshooting

- Router/agent model calls require providers. If you don’t have API keys or a local LM Studio server, `run` may fail. You can still use `logs` and `scrub` on existing runs.
- If you see timeouts from your primary provider, LATTICE falls back per configured order.


Development Map (selected files)

- `src/lattice/router.py` — RouterRunner (modes, huddles, knowledge signals, finalization)
- `src/lattice/agents.py` — Frontend/Backend/LLM/Test agents
- `src/lattice/stage_gates.py` — Stage gates + evaluator with provenance
- `src/lattice/huddle.py` — DecisionSummary model (with `sources[]`) + parser/saver
- `src/lattice/plan.py` — PlanGraph (nodes/edges/modes/reasons)
- `src/lattice/knowledge.py` — KnowledgeBus (drop‑in ingestion + logging)
- `src/lattice/provenance.py` — EvidenceRef types + helpers
- `src/lattice/finalize.py` — Finalization pass (drift, decision log, citations, zip)
- `src/lattice/cli.py` — CLI entry (run/logs/scrub)
LATTICE — LLM‑Driven Router Orchestrator (M4.5)

LATTICE is a CLI‑only, single‑process orchestration runtime. It coordinates multiple sub‑agents (Backend, Frontend, LLM API, and Tests) and uses a Router LLM to plan and act via a structured tool interface. The host runtime enforces stage gates, persists all artifacts and logs, injects DecisionSummaries, and produces a final deliverables bundle with a decision log and citations.


Contents
- Overview
- Key Features
- How It Works
- Requirements
- Installation
- Quick Start
- Configuration (Environment Variables)
- Providers
- Artifacts & Logs
- Stage Gates & Contract Tests
- Huddles & DecisionSummaries
- Router Tools (Function Calls)
- Web Search & RAG
- Safety & Budget Guards
- Troubleshooting
- Development Map


Overview
LATTICE runs a goal‑oriented “agentic loop” in which a Router LLM chooses one action (tool call) per turn. The host executes the tool deterministically, returns an observation, and logs everything. The loop ends when the Router calls finalize_run, or a safety budget is reached. Sub‑agents generate scaffolds and tests; stage gates decide when it is safe to advance. All outputs, logs, decisions, and citations are saved under runs/<run_id>/.


Key Features
- LLM‑driven Router loop with one tool call per turn (no hidden side‑effects).
- Tooling for mode/multitrack planning, huddles, decision recording and injection, agent scheduling, RAG/web search, contract tests, artifact I/O, gate advancement, and finalization.
- Host‑enforced invariants: stage gates are authoritative, artifacts are sandboxed, logs are immutable.
- Provenance and citations: DecisionSummaries can carry EvidenceRef entries; finalization builds a Decision Log and a machine‑readable Citation Index.
- Deliverables zip with generated scaffolds and docs.


How It Works
1) The Router LLM receives a condensed run state (PlanGraph snapshot, active gates/tests, any unread huddle requests, recent DecisionSummaries, and the tool manifest).
2) The Router LLM issues exactly one action by calling a tool. The host executes it and returns an observation.
3) The loop continues until the Router calls finalize_run or reaches the safety budget.
4) The host enforces stage gates via propose_advance_step and runs finalization (tests summary, drift, deliverables, decision log, citations).

Execution modes are Ladder, Tracks, or Weave. By default, the Router runs Weave (hybrid): a critical path plus a lightweight docs track. The Router may explicitly switch modes via set_mode.


Requirements
- Python 3.9+
- Network access for remote providers (optional; you can use a local OpenAI‑compatible server)
- pip installable dependencies (requests)


Installation
- Editable install: pip install -e .
- Or run as a module: python -m src.lattice.cli <command>


Quick Start
1) Set provider credentials (examples):
   - Router (Groq): export GROQ_API_KEY=...
   - Agents (Gemini OpenAI‑compat): export GEMINI_API_KEY=...
   - Optional local fallback (LM Studio): ensure it’s running at http://localhost:1234/v1

2) Run a goal:
   - lattice run "ship a small CLI + README"

3) Inspect results:
   - Artifacts: runs/<run_id>/artifacts/
   - Log: runs/<run_id>/run.jsonl
   - Summary: runs/<run_id>/artifacts/run_summary.json
   - Finalization report: runs/<run_id>/artifacts/finalization/report.json
   - Deliverables: runs/<run_id>/artifacts/deliverables/deliverables.zip

4) Tail logs (nicely formatted model outputs):
   - lattice logs <run_id> --output-only

5) Scrub secrets from prior runs:
   - lattice scrub [<run_id>]


Configuration (Environment Variables)
- Router vs Agent providers
  - LATTICE_ROUTER_PROVIDER_ORDER (default: groq,lmstudio)
  - LATTICE_AGENT_PROVIDER_ORDER (default: gemini-openai-compat,lmstudio)
  - LATTICE_PROVIDER (force both), LATTICE_ROUTER_PROVIDER, LATTICE_AGENT_PROVIDER
  - LATTICE_ROUTER_MODEL, LATTICE_AGENT_MODEL, or LATTICE_MODEL (global)

- Policy & modes
  - LATTICE_ROUTER_POLICY=llm|policy (default: llm — agentic loop; policy = deterministic M4 behavior)
  - LATTICE_MODE=ladder|tracks|weave (default: weave)
  - LATTICE_HUDDLES=dialog|synthesis (default: dialog)

- Web search & RAG
  - LATTICE_WEB_SEARCH=auto|on|off (default: auto; on when Groq is in Router chain or adapter URL provided)
  - LATTICE_WEB_SEARCH_ADAPTER_URL=<url> (optional)
  - LATTICE_USE_RAG=1|0 (default: 1)
  - LATTICE_RAG_MIN_SCORE (default: 0.15), LATTICE_RAG_MAX_INGEST (default: 20)

- Sampling & limits
  - LATTICE_TEMPERATURE (default: 0.2)
  - LATTICE_MAX_TOKENS (optional)
  - LATTICE_ROUTER_MAX_STEPS (default: 32)

- Provider credentials
  - GROQ_API_KEY (Router)
  - GEMINI_API_KEY (Agents)
  - LMSTUDIO_API_KEY (optional; often ignored by server)


Providers
- Groq (OpenAI‑compatible): https://api.groq.com/openai/v1 — Router primary, includes browser_search tool.
- Gemini (OpenAI‑compatible surface): https://generativelanguage.googleapis.com/v1beta/openai/ — Agents default.
- LM Studio (local OpenAI‑compatible): http://localhost:1234/v1 — fallback for Router/Agents.

You can mix and match via provider order and environment overrides.


Artifacts & Logs
All run outputs are under runs/<run_id>/:
- artifacts/
  - backend/ — scaffolded backend (e.g., FastAPI app)
  - frontend/ — scaffolded frontend
  - cli/ — minimal CLI scaffold (argparse)
  - contracts/ — openapi.yaml, tests/, results/
  - decisions/ — DecisionSummary JSON files and decision_log.md
  - huddles/ — huddle record + transcript
  - plans/ — PlanGraph snapshot (JSON)
  - finalization/ — report.json (tests, drift, citations, deliverables)
  - deliverables/deliverables.zip — bundle of key artifacts
  - index.json — artifact index
- run.jsonl — append‑only JSONL log (every model turn, tool call, gate eval)
- config.json — sanitized run configuration


Stage Gates & Contract Tests
- Default stage gates (IDs):
  - sg_api_contract: tests.pass('api_contract')
  - sg_be_scaffold: tests.pass('api_contract') and artifact.exists('backend/**')
  - sg_fe_scaffold: artifact.exists('frontend/**')
  - sg_smoke: tests.pass('smoke_suite')

- Contract test types:
  - schema (OpenAPI heuristic validation)
  - unit (file_exists and similar assertions)
  - http (simple example validation)

Results are saved under artifacts/contracts/results/*.json and are referenced in gate evidence.


Huddles & DecisionSummaries
- Huddles align interfaces/contracts. A Router LLM synthesizes 1–3 DecisionSummaries.
- DecisionSummary fields: id, topic, options[], decision, rationale, risks[], actions[], contracts[], links[], sources[] (EvidenceRef).
- EvidenceRef types supported in DS and in gate evaluations:
  - { "type":"artifact", "id":"artifacts/...", "hash":"sha256:..." }
  - { "type":"rag_doc", "id":"doc-id", "score":0.42, "hash?":"..." }
- The Router injects compact DecisionSummary snippets into agent contexts to keep prompts lean.


Router Tools (Function Calls)
The Router LLM can call exactly one tool per turn. The host validates parameters, executes deterministically, and returns a normalized observation.

Core tools exposed:
- set_mode { target_mode: ladder|tracks|weave, reason }
- open_huddle { topic, attendees[], agenda }
- record_decision_summary { topic, options[], decision?, rationale?, risks[]?, actions[]?, contracts[]?, sources[]?, links[]? }
- inject_summary { decision_id, targets[] }
- spawn_agents { roles:[frontend|backend|llmapi|tests], reason }
- schedule_slice { active_agents:["agent:..."], notes? }
- rag_search { query, top_k }
- web_search { query, top_k, time_range?, engines? }  (visible only when enabled)
- run_contract_tests { tests:[ids] }
- propose_advance_step { step_id, note? }  (host enforces stage gates)
- write_artifact { path:"artifacts/...", content, mime?, tags? }
- read_artifact { path:"artifacts/..." }
- finalize_run { summary }

Observations include applied flags, IDs, lists of artifacts, test results, failed gate evidence, and paths/hashes where applicable. Every action/observation is logged in run.jsonl.


Web Search & RAG
- RAG: The host pre‑ingests selected local files (README*, docs/**, documentation.txt) into a simple TF‑IDF index. The Router and agents can query them via rag_search.
- Web search: If the Router’s provider supports it (Groq browser_search) or a local adapter is configured, web_search is exposed. If not available, the tool returns a clean, non‑fatal error (tool_unavailable).


Safety & Budget Guards
- Stage gates are authoritative — the only path forward is via propose_advance_step; the host evaluates gates and blocks on failure.
- Artifact writes are sandboxed under artifacts/; paths outside are rejected.
- Logs are immutable; every model turn and tool call is recorded.
- Budget guards (host‑enforced):
  - Max actions per slice (agent cap) — excess agents are skipped and logged.
  - Max concurrent huddles — huddle requests above the limit return a non‑fatal error observation.
  - Cooldown after repeated gate failures — observations include a retry suggestion.


Troubleshooting
- No outputs? Check run.jsonl for provider errors or missing credentials.
- Contract tests not running? Ensure the Tests agent produced artifacts/contracts/tests/contract_tests.json.
- Web search not available? Confirm LATTICE_WEB_SEARCH and provider setup; otherwise expect a tool_unavailable observation.
- To inspect model outputs quickly: lattice logs <run_id> --output-only
- To remove secrets from archived runs: lattice scrub [<run_id>]


Development Map (Selected Files)
- src/lattice/router.py — RouterRunner (agentic loop, tools, logging, budget guards)
- src/lattice/router_llm.py — Router LLM calls (plain + tool‑enabled)
- src/lattice/agents.py — Frontend/Backend/LLM/Test agents (scaffolds + tests)
- src/lattice/stage_gates.py — Stage gates + evaluator with evidence
- src/lattice/contracts.py — Contract test runner (schema/http/unit)
- src/lattice/rag.py — Lightweight TF‑IDF RAG index
- src/lattice/huddle.py — DecisionSummary model + persistence + transcript saver
- src/lattice/finalize.py — Finalization (tests, drift, decision log, citations, deliverables)
- src/lattice/cli.py — CLI (`run`, `logs`, `scrub`)
