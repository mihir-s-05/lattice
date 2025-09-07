LATTICE — Multi‑Agent Router, Contracts, and Stage Gates

Overview

LATTICE is a CLI‑only, single‑process system that orchestrates multiple specialized agents through a deterministic Router. It plans work, convenes huddles to reach API/contract decisions, scaffolds backend and frontend code, defines and executes contract tests, evaluates stage gates, indexes artifacts for RAG, and logs everything as structured JSONL.

Highlights

- Router LLM: Planning, refinement, and huddle synthesis with provider fallback.
- Agents: Backend, Frontend, LLM‑API, and Tests agents producing concrete artifacts.
- Contracts & Gates: Portable JSON contract tests + boolean stage gates with traces.
- Huddles: Dialog or synthesis modes, DecisionSummaries saved and injected into prompts.
- Artifacts & RAG: Per‑run artifacts (`runs/<run_id>/artifacts/`), locally indexed for retrieval.
- Logging: Full fidelity JSONL of model calls, plans, huddles, tests, gates, and artifacts.

Quick Start

```bash
pip install -e .

# Run a goal
lattice run "Build FE+BE skeleton for a notes app"

# Pick router provider/model and huddles mode (dialog|synthesis)
lattice run --router-provider groq \
            --router-model openai/gpt-oss-120b \
            --huddles synthesis \
            "Generate a CRUD notes app"

# Inspect logs and outputs
lattice logs -O -f <run_id>
```

Requirements

- Python 3.9+
- Outbound network for Groq/Gemini, or a local LM Studio server
- To run the backend scaffold: `fastapi`, `uvicorn` (generated in requirements.txt)

Configuration (OpenAI‑compatible)

```bash
# Groq (Router LLM)
export GROQ_API_KEY=...
export GROQ_BASE_URL=${GROQ_BASE_URL:-https://api.groq.com/openai/v1}

# Gemini (agents, via OpenAI‑compatible endpoint)
export GEMINI_API_KEY=...
export GEMINI_BASE_URL=${GEMINI_BASE_URL:-https://generativelanguage.googleapis.com/v1beta/openai/}

# LM Studio (local fallback)
export LMSTUDIO_BASE_URL=${LMSTUDIO_BASE_URL:-http://localhost:1234/v1}
```

Provider Selection (role‑scoped)

- Orders:
  - `LATTICE_ROUTER_PROVIDER_ORDER=groq,lmstudio`
  - `LATTICE_AGENT_PROVIDER_ORDER=gemini-openai-compat,lmstudio`
- Pins (preferred provider and/or model):
  - `LATTICE_ROUTER_PROVIDER=groq`
  - `LATTICE_ROUTER_MODEL=openai/gpt-oss-120b`
  - `LATTICE_AGENT_PROVIDER=gemini-openai-compat`
  - `LATTICE_AGENT_MODEL=gemini-2.5-flash-lite`
- Other:
  - `LATTICE_TEMPERATURE` (default 0.2)
  - `LATTICE_MAX_TOKENS`
  - `LATTICE_RAG_MAX_INGEST` (default 20)
  - `LATTICE_HUDDLES` (`dialog`|`synthesis`) — default `dialog`

CLI Commands

- `lattice run <prompt>`
  - Flags: `--router-provider`, `--router-model`, `--huddles dialog|synthesis`
  - Prints artifact dir, log path, run summary, and provider mix
- `lattice logs <run_id>`
  - `-O, --output-only` to show only model outputs
  - `-f, --follow` to tail the log
- `lattice scrub [<run_id>]`
  - Redacts secrets in `config.json` and `run.jsonl` for one or all runs

Architecture

- Router (RouterRunner): Deterministic scheduler with two modes
  - Ladder: contracts → backend_scaffold → frontend_scaffold → smoke_tests
  - Tracks: single slice of all agents, then sync on tests/gates
- Router LLM (RouterLLM):
  - `plan_init(goal)`: produce a compact PlanSpec (saved to `artifacts/plans/router_plan.txt`)
  - `refine_step(summary)`: adjust guidance using tests/gates
  - `huddle(topic, questions, proposed_contract)`: return 1–3 DecisionSummary JSON objects
- Agents:
  - BackendAgent: proposes OpenAPI and generates FastAPI scaffold
  - FrontendAgent: produces wireframes and a static FE scaffold
  - LLMApiAgent: adapter notes, prompt I/O schema, backend adapter stubs
  - TestAgent: contract tests (schema/http/unit), smoke assertions
- Contracts & Stage Gates:
  - ContractRunner executes JSON test specs, saves results
  - GateEvaluator resolves boolean conditions (tests.pass, artifact.exists) with traces
- Artifacts & RAG:
  - ArtifactStore saves text files and maintains `artifacts/index.json`
  - RagIndex provides TF‑IDF search over per‑run artifacts and selected repo files
- Logging:
  - RunLogger appends structured JSON lines to `run.jsonl` with secrets redacted

Huddles

- Modes: `dialog` (agents exchange short rounds, then synthesize) or `synthesis` (single LLM pass)
- Persistence:
  - Markdown transcript: `artifacts/huddles/hud_<id>.md` includes Mode, Questions, Transcript, Notes
  - JSON record: `artifacts/huddles/hud_<id>.json` with `mode` and `auto_decision`
- Decisions:
  - DecisionSummaries saved to `artifacts/decisions/ds_*.json`
  - A compact injection block is added to prompts for subsequent agent turns

Artifacts Layout

- `artifacts/index.json` — artifact catalog
- `artifacts/plans/router_plan.txt` — Router LLM plan
- `artifacts/plans/snapshot.json` — per‑step gate/test snapshots
- `artifacts/huddles/hud_*.md|json` — transcripts and records
- `artifacts/decisions/ds_*.json` — DecisionSummaries
- `artifacts/contracts/openapi.yaml` — API spec
- `artifacts/contracts/tests/contract_tests.json` — test specs
- `artifacts/contracts/results/*.json` — results
- `artifacts/backend/**` — FastAPI scaffold (app/main.py, requirements.txt, run.sh)
- `artifacts/frontend/**` — static FE scaffold (app/index.html, app/script.js, app/styles.css, run.sh)
- `artifacts/run_summary.json` — summary: providers (redacted), orders, gate/test statuses, scaffold roots

Logs (run.jsonl)

- `run_start` — config snapshot
- `model_call` — low‑level provider call (request/response, retries, fallback_chain)
- `router_llm_turn` — Router LLM call (phase: init|refine|huddle|inject)
- `agent_model_turn` — sub‑agent LLM call summary
- `artifact_write` — file persisted into artifacts
- `rag_ingest` / `rag_ingest_agent` / `rag_search` — RAG activity
- `contract_test_run` / `contract_test_result` — contract tests
- `stage_gate_condition` / `stage_gate_trace` / `stage_gate_result` — gate evaluations
- `huddle_request` / `huddle_open` / `huddle_message` / `huddle_close` / `huddle_complete` — huddle lifecycle
- `decision_summary` — DecisionSummary saved
- `run_complete` — pointers to summary

Running the Scaffolds Locally

- Backend (FastAPI):
  ```bash
  cd runs/<run_id>/artifacts/backend
  python3 -m venv .venv && . .venv/bin/activate
  pip install -r requirements.txt
  ./run.sh  # port 8000 by default
  ```
- Frontend (static):
  ```bash
  cd runs/<run_id>/artifacts/frontend
  ./run.sh  # serves ./app via python http.server on $PORT (default 5173)
  ```

Module Guide (src/lattice)

- cli.py — CLI entrypoints and UX
  - Commands: `run`, `logs`, `scrub`
  - New flags on `run`: `--router-provider`, `--router-model`, `--huddles`
  - Prints artifact dir, log path, summary, and provider mix
- router.py — RouterRunner
  - Orchestrates Ladder/Tracks, calls RouterLLM, huddles, agents, ContractRunner, and GateEvaluator
  - Saves plan snapshots and run_summary.json
- router_llm.py — RouterLLM
  - Role‑scoped provider order and model overrides
  - Emits `router_llm_turn` events
- agents.py — Agents and base contract
  - BaseAgent utilities (model calling, artifact writing, RAG)
  - FrontendAgent, BackendAgent, LLMApiAgent, TestAgent
- contracts.py — ContractRunner
  - Runs schema/http/unit tests from JSON and saves results
- stage_gates.py — Gate system
  - StageGate dataclass and GateEvaluator with boolean expression parsing
- huddle.py — Huddles and decisions
  - DecisionSummary/HuddleRecord, parsing, saving transcripts and JSON records
  - Adds `mode` and `auto_decision`; records dialog transcripts
- config.py — Provider and run configuration
  - Router vs Agent provider orders, defaults, and model pins
  - Gemini provider via OpenAI‑compat endpoint (`gemini-openai-compat`)
- providers.py — OpenAI‑compat provider and fallback orchestration
  - Adds per‑provider `model_overrides` and logs `fallback_chain`
- artifacts.py — ArtifactStore for text outputs with catalog
- rag.py — Per‑run TF‑IDF index with search
- runlog.py — JSONL logger with secret redaction
- transcript.py — Human‑readable run transcript builder
- ids.py — ULID generator
- secrets.py — Recursive secret redaction
- worker.py — Worker runner (used only for repo RAG pre‑ingest helper)
- __main__.py — Module entry to CLI
- __init__.py — Package export/version

Notes

- RAG pre‑ingest runs during setup to index select repo files.
- Providers in summaries are redacted for safety; full details are in `run.jsonl`.
