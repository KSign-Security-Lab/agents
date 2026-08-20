# ===========================================================================
#  api — FastAPI + LangGraph. Deliberately torch-free and small: all GPU work
#  is delegated to the `infer` sidecar over HTTP.
# ===========================================================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY docker/requirements/api.txt /tmp/api.txt
RUN pip install -r /tmp/api.txt

WORKDIR /app
COPY api/ /app/api/

ENV PYTHONPATH=/app \
    STORAGE_ROOT=/storage
EXPOSE 8000
CMD ["uvicorn", "api.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
