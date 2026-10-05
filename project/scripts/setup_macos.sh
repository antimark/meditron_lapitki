#!/usr/bin/env bash
set -euo pipefail
ENV_NAME="${CARDIO_CONDA_ENV:-cardio-extractor}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if ! command -v conda >/dev/null 2>&1; then
  echo "conda not found. Install Miniforge (Apple Silicon/Intel as appropriate) or Miniconda first." >&2
  exit 1
fi
if conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
  echo "Using existing env: $ENV_NAME"
else
  echo "Creating env: $ENV_NAME (Python 3.11)"
  conda create -y -n "$ENV_NAME" python=3.11 pip
fi
conda run -n "$ENV_NAME" python -m pip install --upgrade pip setuptools wheel
conda run -n "$ENV_NAME" python -m pip install -r requirements.txt
conda run -n "$ENV_NAME" python scripts/check_environment.py
