#!/usr/bin/env bash
# Update the hosted MCP server: fast-forward this checkout, sync the venv to
# the lockfile, and restart the systemd unit.
#
# Run from anywhere:
#   deploy/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."

SERVICE=ouro-mcp
PORT=8095

# This checkout's .python-version names a pyenv env that is not installed
# here, and the pyenv `uv` shim then refuses to run. Call a real binary.
if [[ -n ${UV:-} ]]; then
  :
elif [[ -x "$HOME/.local/bin/uv" ]]; then
  UV=$HOME/.local/bin/uv
elif [[ -x /home/matt/.pyenv/versions/3.10.12/envs/materials/bin/uv ]]; then
  UV=/home/matt/.pyenv/versions/3.10.12/envs/materials/bin/uv
else
  echo "uv not found; set UV to the uv binary" >&2
  exit 1
fi

if [[ -x .venv/bin/python ]]; then
  PYTHON=$(readlink -f .venv/bin/python)
else
  PYTHON=/usr/bin/python
fi

git pull --ff-only

UV_PYTHON=$PYTHON "$UV" sync --frozen --python "$PYTHON"

sudo systemctl restart "$SERVICE"

ready=
for _ in 1 2 3 4 5 6 7 8 9 10; do
  code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:${PORT}/mcp" || true)
  if [[ $code == 401 ]]; then
    ready=1
    break
  fi
  sleep 0.3
done

if [[ -z $ready ]]; then
  echo "ouro-mcp did not answer on 127.0.0.1:${PORT} after restart" >&2
  systemctl status "$SERVICE" --no-pager || true
  exit 1
fi

echo "Deployed $(git rev-parse --short HEAD) (ouro-mcp $(.venv/bin/python -c 'import importlib.metadata as m; print(m.version("ouro-mcp"))'), ouro-py $(.venv/bin/python -c 'import importlib.metadata as m; print(m.version("ouro-py"))'))"
