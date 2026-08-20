#!/usr/bin/env bash
# Pre-download all model weights onto /data so first start-up is not a 30-minute
# silent wait. Safe to re-run: snapshot_download resumes and skips complete files.
set -euo pipefail

cd "$(dirname "$0")/.."
set -a; . docker/.env; set +a

: "${MODEL_DIR:?}"; : "${INFER_MODEL_DIR:?}"
mkdir -p "$MODEL_DIR" "$INFER_MODEL_DIR"

# The vllm image is already present locally and carries huggingface_hub +
# hf_transfer, so we reuse it as the downloader instead of pulling another image.
DL_IMAGE="vllm/vllm-openai:latest"

pull() {  # pull <repo_id> <host_cache_dir>
  local repo="$1" dir="$2"
  echo "==> $repo -> $dir"
  docker run --rm \
    -v "$dir:/hf" \
    -e HF_HOME=/hf \
    -e HF_HUB_ENABLE_HF_TRANSFER=1 \
    --entrypoint python3 "$DL_IMAGE" -c "
from huggingface_hub import snapshot_download
p = snapshot_download('$repo', max_workers=8)
print('done:', p)
"
}

echo '### LLM (served by vLLM) ###'
pull "$MODEL_ID" "$MODEL_DIR"

echo '### embeddings / reranker / ASR (served by the infer sidecar) ###'
pull "$EMBED_MODEL"  "$INFER_MODEL_DIR"
pull "$RERANK_MODEL" "$INFER_MODEL_DIR"
pull "Systran/faster-whisper-${ASR_MODEL}" "$INFER_MODEL_DIR"

echo
echo "==> disk usage"
du -sh "$MODEL_DIR" "$INFER_MODEL_DIR"
df -h /data | tail -1
