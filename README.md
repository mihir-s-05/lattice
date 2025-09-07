LATTICE — CLI Orchestrator with Weave Mode, Replanning, Provenance, and Finalization (M4)

LATTICE is a CLI‑only, single‑process orchestration runtime. It coordinates multiple agents via a deterministic Router, runs contract tests and stage gates, captures provenance, supports mid‑flight replanning, and emits a rigorous finalization report and deliverables.

Highlights (Milestone 4)
- Weave mode: Hybrid execution (Ladder on the critical path + a Docs track in parallel).
- Mid‑flight mode switching with structured reasons and huddles.
- Provenance & citations plumbing (artifacts + RAG docs) with a knowledge signal hook.
- Finalization pass: drift checks, decision log with inline citations, citation index, deliverables zip.


Getting Started

- Requirements: Python 3.9+, `requests` (installed via setup), provider credentials if using remote models.
- Install:
  - pip: `pip install -e .`
  - or module: run via `python -m src.lattice.cli ...` from repo root.

CLI Commands

- lattice run "<goal>"
  - Orchestrates Router + agents to plan, scaffold, test, evaluate gates, replan if needed, and finalize.
  - Emits `runs/<run_id>/artifacts/*`, structured logs `run.jsonl`, PlanGraph, DecisionSummaries, knowledge journal, and a finalization report.

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


Key Concepts

- Router modes
  - Ladder: strictly ordered critical path.
  - Tracks: parallel slices across agents.
  - Weave (M4): Ladder on the critical path, with a Docs/README track in parallel. A minimal PlanGraph is saved to `artifacts/plans/plan_graph.json` with nodes, edges, segment modes, and `reasons[]` (switch/replan causes).

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

1) Run Weave for a CLI app with docs in parallel
   - `lattice run "ship a small CLI + README"`
   - If your goal mentions “readme”/“docs” the Router will prefer Weave. Critical path: contracts → backend scaffold → smoke tests. Docs track drafts a README in parallel.

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

