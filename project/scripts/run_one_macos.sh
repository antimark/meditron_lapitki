#!/usr/bin/env bash
set -euo pipefail
ENV_NAME="${CARDIO_CONDA_ENV:-cardio-extractor}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec conda run --no-capture-output -n "$ENV_NAME" python process_one.py "$@"
