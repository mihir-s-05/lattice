set -eu
PORT=${PORT:-5173}
cd "$(dirname "$0")"/app
python3 -m http.server "$PORT"