#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

python3 -m venv .venv 2>/dev/null || true
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

export PUBLIC_BASE="${PUBLIC_BASE:-http://localhost:8000}"
echo "NuEvo API starting on $PUBLIC_BASE  (docs at $PUBLIC_BASE/docs)"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
