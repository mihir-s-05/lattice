LATTICE (Milestone 1) — CLI Worker Runner

Overview

LATTICE is a CLI-only, single-process “worker runner” that executes one LLM turn with a provider abstraction, per-run artifact store, minimal local vector search (RAG), and detailed JSONL logging.

Features

- Provider abstraction: Groq, Google Gemini (OpenAI-compatible), LM Studio (local).
- Fallback chain: Groq → Gemini → LM Studio with retries/backoff on 429/5xx.
- Per-run artifacts: writes outputs under `./runs/<run_id>/` and catalogs them.
- Minimal RAG: local TF‑IDF index with simple similarity search and query/hit logging.
- Extreme logging: JSONL capturing prompts, outputs, raw responses, RAG traces, errors.

Requirements

- Python 3.9+
- Network access for Groq/Gemini, or a local LM Studio server.

Installation

```bash
pip install -e .
```

Configuration

Set at least one provider. Groq is preferred; Gemini or LM Studio are fallbacks.

```bash
# Groq (preferred)
export GROQ_API_KEY=...                  # base URL defaults to https://api.groq.com/openai/v1
# optional override
export GROQ_BASE_URL=https://api.groq.com/openai/v1

# Gemini (OpenAI-compatible surface)
export GEMINI_API_KEY=...
# optional override
export GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/

# LM Studio (local)
# optional override; common default is http://localhost:1234/v1
export LMSTUDIO_BASE_URL=http://localhost:1234/v1
```

Additional environment variables:

- LATTICE_PROVIDER_ORDER: comma list (default `groq,gemini,lmstudio`).
- LATTICE_PROVIDER: force a single provider (e.g., `lmstudio`).
- LATTICE_MODEL: override model for the chosen provider.
- LATTICE_BASE_URL: override base URL for the chosen provider.
- LATTICE_USE_RAG: `1`/`0` (default `1`).
- LATTICE_TEMPERATURE: float (default `0.2`).
- LATTICE_MAX_TOKENS: integer.
- LATTICE_RAG_MAX_INGEST: max repo files to pre‑ingest (default `20`).

Usage

Run a single prompt and print the artifact and log paths:

```bash
lattice run "summarize this repo README"
```

You can also run without installing the console script:

```bash
python -m lattice run "summarize this repo README"
```

Logs

- Show logs for a run:
  ```bash
  lattice logs <run_id>
  ```
- Follow (tail -f) the log:
  ```bash
  lattice logs --follow <run_id>
  ```

Output & Run Artifacts

Each run creates a directory: `./runs/<run_id>/` containing:

- `config.json` — run configuration snapshot (keys masked).
- `run.jsonl` — rich JSONL log: timestamps, provider, model, prompts, outputs, raw responses, retries/errors, RAG queries/hits.
- `artifacts/index.json` — artifact catalog (path, mime, hash, tags, metadata).
- `artifacts/output.txt` — the final model output.

RAG (Vector Search)

- Minimal local TF‑IDF index stored per run.
- Pre‑ingests common repo docs (e.g., `README*`, `documentation.txt`, `docs/**/*.md|.txt`) for retrieval.
- Searches the index and attaches short context snippets to the model prompt when relevant.
- Logs `rag_ingest` events plus `rag_queries` and `rag_hits` for full traceability.

Providers

- Groq (OpenAI-compatible): base `https://api.groq.com/openai/v1`, models like `openai/gpt-oss-20b`.
- Gemini (OpenAI-compatible surface): base `https://generativelanguage.googleapis.com/v1beta/openai/`, models like `gemini-2.5-flash`.
- LM Studio (local OpenAI-compatible): base typically `http://localhost:1234/v1`.

Fallback Behavior

The runner will try each provider in `LATTICE_PROVIDER_ORDER`. On 429/5xx it retries with backoff, then falls back to the next provider. Every attempt is logged with the full prompt, raw response, and errors.

Troubleshooting

- No module named `requests`: run `pip install -e .` again or `pip install requests`.
- Groq/Gemini 401/403: ensure the corresponding `*_API_KEY` is set.
- Groq 429: wait for quota reset or set `LATTICE_PROVIDER_ORDER=gemini,lmstudio`.
- LM Studio connection errors: ensure the local server is running and `LMSTUDIO_BASE_URL` points to it.
- SSL warnings on macOS system Python: create a virtualenv with a newer OpenSSL or ignore warnings if calls succeed.

Development

- Run from source without installing the script:
  ```bash
  PYTHONPATH=src python3 -m lattice run "hello world"
  ```
- Code is under `src/lattice/`. Console script entrypoint is `lattice`.
