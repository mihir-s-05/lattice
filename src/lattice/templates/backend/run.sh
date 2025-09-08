#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"/app
exec python3 -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"