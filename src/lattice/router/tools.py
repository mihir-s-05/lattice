from __future__ import annotations

from typing import Any, Dict, List


def _tool_schema(name: str, desc: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": params
        }
    }


def build_tools_manifest() -> List[Dict[str, Any]]:
    tools: List[Dict[str, Any]] = []

    tools.append(_tool_schema(
        "set_mode",
        "Select Router execution mode (ladder|tracks|weave) with rationale.",
        {
            "type": "object",
            "properties": {
                "target_mode": {"type": "string", "enum": ["ladder", "tracks", "weave"]},
                "reason": {"type": "string"},
            },
            "required": ["target_mode", "reason"],
        },
    ))

    tools.append(_tool_schema(
        "open_huddle",
        "Open a huddle to align interfaces/contracts and record a transcript start.",
        {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "attendees": {"type": "array", "items": {"type": "string"}},
                "agenda": {"type": "string"},
                "questions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["topic"],
        },
    ))

    tools.append(_tool_schema(
        "request_huddle",
        "Request a huddle/check-in for later (queues it for the router to open).",
        {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "questions": {"type": "array", "items": {"type": "string"}},
                "attendees": {"type": ["array", "null"], "items": {"type": "string"}},
                "agenda": {"type": ["string", "null"]},
                "from": {"type": ["string", "null"]},
                "urgency": {"type": ["string", "null"], "enum": ["low", "normal", "high", None]},
            },
            "required": ["topic"],
        },
    ))

    tools.append(_tool_schema(
        "record_decision_summary",
        "Persist a DecisionSummary JSON and update decision log.",
        {
            "type": "object",
            "properties": {
                "huddle_id": {"type": ["string", "null"]},
                "topic": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
                "decision": {"type": ["string", "null"]},
                "rationale": {"type": ["string", "null"]},
                "risks": {"type": "array", "items": {"type": "string"}},
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "owner": {"type": "string"},
                            "task": {"type": "string"},
                            "due": {"type": ["string", "null"]},
                        },
                        "required": ["owner", "task"],
                    },
                },
                "contracts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "schema_hash": {"type": "string"},
                        },
                        "required": ["name", "schema_hash"],
                    },
                },
                "sources": {"type": "array", "items": {"type": "object"}},
                "links": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["topic", "options"],
        },
    ))

    tools.append(_tool_schema(
        "inject_summary",
        "Inject a compact DecisionSummary snippet into target contexts (router or agents).",
        {
            "type": "object",
            "properties": {
                "decision_id": {"type": "string"},
                "targets": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["decision_id", "targets"],
        },
    ))

    tools.append(_tool_schema(
        "spawn_agents",
        "Create missing agent instances if not already active.",
        {
            "type": "object",
            "properties": {
                "roles": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["frontend", "backend", "llmapi", "tests"]},
                },
                "reason": {"type": "string"},
            },
            "required": ["roles", "reason"],
        },
    ))

    tools.append(_tool_schema(
        "schedule_slice",
        "Run one concurrent slice across selected agents (plan/act/report) and persist outputs.",
        {
            "type": "object",
            "properties": {
                "active_agents": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": ["string", "null"]},
                "parallel": {"type": "boolean"},
                "timeout_sec": {"type": ["integer", "null"], "minimum": 5, "maximum": 1800},
            },
            "required": ["active_agents"],
        },
    ))

    tools.append(_tool_schema(
        "destroy_agents",
        "Destroy/unregister agent instances (frees context window) if they are active.",
        {
            "type": "object",
            "properties": {
                "roles": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": ["string", "null"]},
            },
            "required": ["roles"],
        },
    ))

    tools.append(_tool_schema(
        "set_agent_permissions",
        "Set or modify per-agent write permissions (used in tracks mode to prevent cross-agent edits).",
        {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "mode": {"type": "string", "enum": ["set", "add", "remove"]},
                "allow_globs": {"type": "array", "items": {"type": "string"}},
                "deny_globs": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": ["string", "null"]},
            },
            "required": ["role", "mode"],
        },
    ))

    tools.append(_tool_schema(
        "get_agent_permissions",
        "Get current per-agent write permissions.",
        {
            "type": "object",
            "properties": {"role": {"type": ["string", "null"]}},
        },
    ))

    tools.append(_tool_schema(
        "rag_search",
        "Query the run-scoped vector index for relevant artifacts/transcripts.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                "where": {
                    "type": ["object", "null"],
                    "properties": {
                        "doc_id_prefix": {"type": ["string", "null"]},
                        "path_prefix": {"type": ["string", "null"]},
                        "path_contains": {"type": ["string", "null"]},
                        "tags_any": {"type": "array", "items": {"type": "string"}},
                        "tags_all": {"type": "array", "items": {"type": "string"}},
                        "kind": {"type": ["string", "null"]},
                    },
                },
            },
            "required": ["query", "top_k"],
        },
    ))

    tools.append({
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Perform a web search via Groq browser_search or a local adapter.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
                    "time_range": {"type": ["string", "null"], "enum": ["d", "w", "m", "y", None]},
                    "engines": {"type": ["string", "null"]},
                    "language": {"type": ["string", "null"]},
                    "pageno": {"type": ["integer", "null"], "minimum": 1},
                },
                "required": ["query", "top_k"],
            },
        },
    })

    tools.append(_tool_schema(
        "run_contract_tests",
        "Run selected contract tests by id and return structured results.",
        {
            "type": "object",
            "properties": {
                "tests": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["tests"],
        },
    ))

    tools.append(_tool_schema(
        "propose_advance_step",
        "Request advancement to the next step; Router enforces stage gates.",
        {
            "type": "object",
            "properties": {
                "step_id": {"type": "string"},
                "note": {"type": ["string", "null"]},
            },
            "required": ["step_id"],
        },
    ))

    tools.append(_tool_schema(
        "read_artifact",
        "Read an artifact content by path under artifacts/.",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ))

    tools.append(_tool_schema(
        "semantic_search",
        "Semantic search over run-scoped artifacts/transcripts (includes huddle transcripts/summaries).",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                "where": {
                    "type": ["object", "null"],
                    "properties": {
                        "doc_id_prefix": {"type": ["string", "null"]},
                        "path_prefix": {"type": ["string", "null"]},
                        "path_contains": {"type": ["string", "null"]},
                        "tags_any": {"type": "array", "items": {"type": "string"}},
                        "tags_all": {"type": "array", "items": {"type": "string"}},
                        "kind": {"type": ["string", "null"]},
                    },
                },
            },
            "required": ["query", "top_k"],
        },
    ))

    tools.append(_tool_schema(
        "coherence_check",
        "Run lightweight coherence checks over the workspace to detect conflicting sources of truth (e.g., duplicate OpenAPI specs, frontend env mismatches).",
        {
            "type": "object",
            "properties": {
                "max_findings": {"type": ["integer", "null"], "minimum": 1, "maximum": 50},
                "paths": {"type": ["array", "null"], "items": {"type": "string"}},
                "changed_since": {"type": ["number", "null"]},
            },
        },
    ))

    tools.append(_tool_schema(
        "write_artifact",
        "Write a text artifact under artifacts/ in the run directory (run-scoped).",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["path", "content"],
        },
    ))

    tools.append(_tool_schema(
        "create_checklist",
        "Create or replace the run checklist based on the goal.",
        {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "description": {"type": "string"},
                            "status": {"type": "string", "enum": ["pending", "done", "skipped", "blocked"]},
                            "required": {"type": "boolean"},
                            "owner": {"type": "string"},
                            "note": {"type": ["string", "null"]},
                            "parent_id": {"type": ["string", "null"]},
                        },
                        "required": ["id", "description"],
                    },
                },
            },
            "required": ["items"],
        },
    ))

    tools.append(_tool_schema(
        "update_checklist",
        "Update run checklist items (done|skipped|blocked|pending) with optional notes.",
        {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "status": {"type": "string", "enum": ["pending", "done", "skipped", "blocked"]},
                            "note": {"type": ["string", "null"]},
                            "description": {"type": ["string", "null"]},
                            "required": {"type": ["boolean", "null"]},
                            "owner": {"type": ["string", "null"]},
                            "parent_id": {"type": ["string", "null"]},
                            "action": {"type": ["string", "null"], "enum": ["add", "update", "remove", None]},
                        },
                        "required": ["id", "status"],
                    },
                },
            },
            "required": ["items"],
        },
    ))

    tools.append(_tool_schema(
        "write_file",
        "Write a file to the workspace (relative to project root unless absolute).",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "mode": {"type": "string", "enum": ["overwrite", "append"]},
            },
            "required": ["path", "content"],
        },
    ))

    tools.append(_tool_schema(
        "read_file",
        "Read a file from the workspace (relative to project root unless absolute).",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_bytes": {"type": "integer", "minimum": 1, "maximum": 500000},
            },
            "required": ["path"],
        },
    ))

    tools.append(_tool_schema(
        "delete_file",
        "Delete a file from the workspace (relative to project root unless absolute). Prefer this over shelling out.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "missing_ok": {"type": ["boolean", "null"]},
            },
            "required": ["path"],
        },
    ))

    tools.append(_tool_schema(
        "run_command",
        "Run a shell command in the workspace. Must be safe and justified.",
        {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": ["string", "null"]},
                "timeout_sec": {"type": "integer", "minimum": 1, "maximum": 600},
                "reason": {"type": "string"},
            },
            "required": ["command", "reason"],
        },
    ))

    tools.append(_tool_schema(
        "finalize_run",
        "Finalize the run: run tests/linters, consolidate citations, create deliverables.",
        {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    ))

    return tools


ROUTER_SYSTEM_PROMPT = (
    "You are the Router LLM: you decide modes (ladder|tracks|weave), open huddles, write DecisionSummaries, "
    "spawn/schedule agents, read/write files, run commands, run tests, and finalize. You must act ONLY via tools - do not claim to edit files or advance steps without tools. "
    "Always write code and project files via write_file in the workspace. Never use artifacts for code output. "
    "At the start of a run, create a checklist via create_checklist based on the goal. Keep it dynamic: add/remove/update items via update_checklist as scope changes. Finalization is blocked until required checklist items are done. "
    "Log decisions explicitly via record_decision_summary (and update the checklist as work completes); these are captured in the transcript. "
    "Pick a single stack and stick to it: do NOT mix frameworks or generate parallel scaffolds (e.g., do not create both FastAPI and Express, or both static HTML and React). "
    "Never write placeholder/template content; only persist outputs derived from model decisions for this goal. "
    "Assume a fresh environment: install dependencies explicitly (pip/npm) before running tests or servers; do not assume packages are present. "
    "The workspace may be seeded from an unrelated repo; treat pre-existing project files (e.g., top-level `requirements.txt`, `package.json`, `backend/`, `frontend/`) as untrusted unless you created/updated them for THIS goal in THIS run. "
    "When installing deps, prefer project-scoped manifests you wrote for this goal (e.g., `backend/requirements.txt`, `backend/package.json`, `frontend/package.json`) rather than the repo root. "
    "Avoid dependencies that commonly require native builds (e.g., `uvicorn[standard]`, `PyYAML`) unless truly needed; prefer minimal pure-Python/runtime-safe deps (e.g., `uvicorn` without extras) to reduce install failures. "
    "Keep contexts lean: inject only DecisionSummary snippets; use rag_search/web_search for details. "
    "When decisions depend on artifacts or retrieval, include EvidenceRef sources in record_decision_summary.sources. "
    "Always call spawn_agents before schedule_slice or open_huddle; guardrails will block if agents are missing. "
    "Spawn relevant subagents before opening a huddle; huddles come after agents exist. "
    "Ask for a huddle when interfaces/ownership are ambiguous. "
    "Stage gates require `tests.pass('api_contract')`. Early in the run, ensure exactly one OpenAPI/Swagger spec exists at `contracts/openapi.yaml` (canonical); if a spec file already exists, OVERWRITE it to match the current goal. Then run contract checks via run_contract_tests (or re-check gates via propose_advance_step after creating the spec). Do NOT create multiple OpenAPI specs in different folders. "
    "Use `run_contract_tests` ONLY for contract/schema checks (manifest-defined ids and the built-in OpenAPI schema validation); do NOT pass pytest nodeids or file paths to `run_contract_tests` — use `run_command` to execute `pytest` instead. "
    "If you choose to define contracts/specs, keep them aligned with implementation and tests. "
    "Never run harmful commands. Obey command allow/deny lists and provide a reason for each command. "
    "Do not fabricate test results. To validate, run tests via run_contract_tests or run_command and use real outputs. "
    "Contract tests must include unique ids and must only reference files that actually exist in the workspace. "
    "NEVER bypass stage gates: request propose_advance_step and accept failures to replan or huddle. "
    "End by calling finalize_run with a concise run summary and pointers to key artifacts."
)
