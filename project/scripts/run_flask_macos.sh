#!/usr/bin/env bash
set -euo pipefail
ENV_NAME="${CARDIO_CONDA_ENV:-cardio-extractor}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec conda run --no-capture-output -n "$ENV_NAME" gunicorn \
  --workers "${GUNICORN_WORKERS:-1}" \
  --threads "${GUNICORN_THREADS:-4}" \
  --timeout "${GUNICORN_TIMEOUT:-600}" \
  --bind "${CARDIO_BIND:-127.0.0.1:5000}" \
  'flask_app:app'
