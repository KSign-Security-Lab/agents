# ===========================================================================
#  Two images, split by what they need rather than by what they do:
#
#    infer  — everything that touches the GPU (embeddings, reranking, ASR).
#             Built ON TOP of the vllm image, which the stack already pulls for
#             serving and which already carries torch + CUDA + transformers.
#             Reusing it costs ~2GB of new layers instead of ~12GB for a second
#             CUDA torch stack, and guarantees one CUDA version across the two.
#
#    worker — everything that touches files (format conversion, layout
#             extraction, chunking). CPU-only torch, because every model that
#             needs a GPU already lives in `infer` and is reached over HTTP.
#             Docling's layout models and EasyOCR run acceptably on CPU at these
#             volumes; set TORCH_INDEX to a CUDA wheel index to move them onto
#             the GPU if OCR throughput becomes the bottleneck.
# ===========================================================================

# ---------------------------------------------------------------------------
#  infer
# ---------------------------------------------------------------------------
FROM vllm/vllm-openai:v0.27.1 AS infer

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# The vllm image sets an entrypoint of its own; we run a plain uvicorn.
ENTRYPOINT []

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
COPY docker/requirements/infer.txt /tmp/infer.txt
# torch/transformers already ship in the base image, so they are excluded from
# the resolve to stop pip replacing a working CUDA build with a PyPI one.
RUN uv pip install --system -r /tmp/infer.txt \
 && python3 -c "import torch; print('torch', torch.__version__); assert '+cu' in torch.__version__"

WORKDIR /app
COPY apps/infer/ /app/infer/

ENV HF_HOME=/models \
    HF_HUB_ENABLE_HF_TRANSFER=0 \
    PYTHONPATH=/app
EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "infer.app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--timeout-keep-alive", "120"]


# ---------------------------------------------------------------------------
#  worker
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS worker

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# LibreOffice converts Office and HWP files to PDF so a single bbox-highlight
# path serves every format.
#   libreoffice-java-common: the JVM bridge H2Orestart needs. Without it
#     `unopkg add` fails with "unexpected error occurred while searching for a
#     Java" even when a JRE is installed.
#   fonts-noto-cjk / fonts-nanum: without Korean fonts the converted PDF renders
#     Korean as tofu boxes, which would also wreck the extracted geometry.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice-writer libreoffice-calc libreoffice-impress \
      libreoffice-java-common default-jre-headless \
      fonts-noto-cjk fonts-noto-cjk-extra fonts-nanum fonts-nanum-extra \
      ffmpeg \
      tesseract-ocr tesseract-ocr-kor tesseract-ocr-eng \
      libgl1 libglib2.0-0 \
      curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# H2Orestart gives LibreOffice native .hwp / .hwpx import.
ARG H2ORESTART_VERSION=v0.7.13
RUN mkdir -p /opt/hwp \
 && curl -fsSL -o /opt/hwp/H2Orestart.oxt \
      "https://github.com/ebandal/H2Orestart/releases/download/${H2ORESTART_VERSION}/H2Orestart.oxt" \
 && HOME=/tmp unopkg add --shared --suppress-license /opt/hwp/H2Orestart.oxt \
 && HOME=/tmp unopkg list --shared | grep -qi h2orestart \
 && echo "H2Orestart registered" \
 && rm -rf /tmp/.config /tmp/.cache

# CPU wheels are ~250MB against ~8.6GB for the CUDA build, and nothing in this
# image needs a GPU.
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
RUN pip install --index-url "${TORCH_INDEX}" torch==2.9.1 torchvision==0.24.1

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
COPY docker/requirements/api.txt /tmp/api.txt
COPY docker/requirements/worker.txt /tmp/worker.txt
RUN uv pip install --system -r /tmp/worker.txt

WORKDIR /app
COPY apps/api/ /app/api/

# LibreOffice needs a writable HOME; docling and easyocr cache models under HF_HOME.
ENV HOME=/tmp \
    HF_HOME=/models \
    STORAGE_ROOT=/storage \
    PYTHONPATH=/app \
    OMP_NUM_THREADS=8
CMD ["arq", "api.worker.main.WorkerSettings"]
