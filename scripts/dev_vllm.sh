#!/usr/bin/env bash
# Serve just the LLM directly on the host via uv — no Docker, no Postgres,
# no gateway. For smoke-testing anything LLM-related (tool calling, prompts)
# without paying for the full compose stack.
#
# Usage: scripts/dev_vllm.sh [GPU_INDEX] [MODEL_ID]
set -euo pipefail

cd "$(dirname "$0")/.."

GPU_INDEX="${1:-0}"
MODEL_ID="${2:-LGAI-EXAONE/EXAONE-4.0-32B-AWQ}"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found. Install it: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

echo "==> serving $MODEL_ID on GPU $GPU_INDEX (host port 8000)"
CUDA_VISIBLE_DEVICES="$GPU_INDEX" uv run --with vllm -- vllm serve "$MODEL_ID" \
  --served-model-name main \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.62 \
  --tensor-parallel-size 1 \
  --host 0.0.0.0 --port 8000 \
  --structured-outputs-config '{"disable_any_whitespace":true,"backend":"xgrammar"}' \
  --tool-call-parser hermes --enable-auto-tool-choice
