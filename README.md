# LATTICE

LATTICE is a local, agentic router that runs multi-step builds/tests in an isolated per-run workspace, logs everything to a run directory, and produces a filtered `deliverables.zip`.

## Install

Requirements: Python 3.9+

```bash
python -m venv venv
# Windows: .\venv\Scripts\activate
# macOS/Linux: source venv/bin/activate

pip install -e ".[test]"
```

## Configure a provider

LATTICE reads provider credentials from environment variables (it does not auto-load a `.env` file).

Use `.env.example` as a reference and export what you need in your shell.

## Run a job

```bash
LATTICE_PROVIDER=openai lattice run -m gpt-5-mini "Build and test a personal portfolio website with animations"
```

PowerShell:

```powershell
$env:LATTICE_PROVIDER="openai"
lattice run -m gpt-5-mini "Build and test a personal portfolio website with animations"
```

Each run prints paths like:

- run dir: `~/.lattice/runs/<run_id>` (Windows: `%USERPROFILE%\.lattice\runs\<run_id>`)
- logs: `run.jsonl`
- workspace: `workspace/`
- artifacts: `artifacts/`

Follow a run live:

```bash
lattice logs <run_id> --follow
```

## Workspaces and deliverables

- Each run gets its own workspace under the run directory.
- By default, the workspace is seeded by copying your current working directory.
- Deliverables are packaged using a run-start freshness baseline: only files under `backend/`, `frontend/`, `public/`, `static/`, and `contracts/` that were created/modified during the run are included (plus contract results under `artifacts/contracts/results/`).

Start from an empty workspace:

```bash
LATTICE_WORKSPACE_SEED=empty lattice run "..."
```

## Local API server (optional)

```bash
lattice serve --host 127.0.0.1 --port 5050
```

This runs a local FastAPI server that exposes endpoints for listing runs and reading artifacts under your runs directory.

## Development

Run tests:

```bash
pytest -q
```

