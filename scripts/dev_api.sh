#!/usr/bin/env bash
# Run the api service directly on the host via uv — no Docker.
#
# Needs Postgres/Redis/infer reachable: either bring up just those with
# Docker (`docker compose --env-file docker/.env -f docker/compose.yml up -d
# postgres redis infer`) or point DATABASE_URL/REDIS_URL/INFER_BASE_URL at
# wherever they already run — the defaults (postgres/redis/infer as
# hostnames) only resolve inside the Compose network. This does not replace
# `make up` — it's for iterating on api code without paying for the whole
# stack every time.
set -euo pipefail

cd "$(dirname "$0")/../apps"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found. Install it: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

# STORAGE_ROOT defaults to /storage (container-only); give it a writable
# local dir unless the caller already set one.
export STORAGE_ROOT="${STORAGE_ROOT:-$(pwd)/../data/storage}"
mkdir -p "$STORAGE_ROOT"

# cwd must be apps/ (not apps/api/): api/__init__.py makes "api" the
# importable package name, and Python resolves it from the parent directory
# — the same reason Docker's api image sets WORKDIR /app then COPY apps/api/
# to /app/api/.
uv run --with-requirements ../docker/requirements/api.txt -- \
  uvicorn api.app.main:app --reload --port 8000
