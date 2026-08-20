#!/usr/bin/env bash
# Run the infer sidecar directly on the host via uv — no Docker.
#
# infer.txt deliberately excludes torch/transformers (in Docker they're
# already satisfied by the vllm base image's CUDA build — see the comment in
# docker/requirements/infer.txt). On a bare host you need a CUDA-enabled
# torch installed separately first, e.g.:
#   uv pip install --python 3.12 torch --index-url https://download.pytorch.org/whl/cu121
# then re-run this script so it reuses that environment instead of building
# a fresh one with --with-requirements.
set -euo pipefail

cd "$(dirname "$0")/../apps"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found. Install it: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

# cwd must be apps/ (not apps/infer/): infer/__init__.py makes "infer" the
# importable package name, and Python resolves it from the parent directory.
uv run --with-requirements ../docker/requirements/infer.txt -- \
  uvicorn infer.app.main:app --reload --port 8001
