#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  One command for local development.
#
#    scripts/dev.sh            deps + migrate + seed, then api and web with reload
#    scripts/dev.sh deps       just Postgres/Redis (for running api in an IDE)
#    scripts/dev.sh api        just api
#    scripts/dev.sh web        just web
#
#  Everything GPU-bound stays on the GPU server; this only ever talks to it over
#  LLM_BASE_URL / INFER_BASE_URL. Config is docker/.env.dev — one file.
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/docker/.env.dev"
COMPOSE="docker compose -f $ROOT/docker/compose.dev.yml"
TARGET="${1:-all}"

die() { echo "error: $*" >&2; exit 1; }
step() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }

[ -f "$ENV_FILE" ] || die "$ENV_FILE not found. Run: make dev-setup"
command -v uv >/dev/null || die "uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"

# Sourced, not passed to compose as --env-file: bash expands the ${GPU_HOST}
# references in the file, and compose interpolates from the environment anyway.
set -a; . "$ENV_FILE"; set +a

# api and the worker container must agree on where uploads live: the worker's
# /storage is this directory bind-mounted.
export STORAGE_ROOT="${STORAGE_ROOT:-$ROOT/docker/${DEV_DATA_ROOT#./}/storage}"
export API_INTERNAL_URL="http://localhost:${API_PORT:-8000}"
mkdir -p "$STORAGE_ROOT"

# ------------------------------------------------------------------ deps
start_deps() {
  step "starting Postgres + Redis"
  # No array expansion: macOS ships bash 3.2, where "${empty[@]}" trips set -u.
  if [ -n "${INGEST:-}" ]; then
    $COMPOSE --profile ingest up -d --wait
  else
    $COMPOSE up -d --wait
  fi
}

# --------------------------------------------------------------- python env
# A real venv rather than `uv run --with-requirements`, so an IDE debugger has
# an interpreter to attach to.
VENV="$ROOT/.venv"
STAMP="$VENV/.api-requirements.sha"
ensure_venv() {
  local want
  want="$(shasum -a 256 "$ROOT/docker/requirements/api.txt" | cut -d' ' -f1)"
  if [ ! -x "$VENV/bin/python" ] || [ "$(cat "$STAMP" 2>/dev/null || true)" != "$want" ]; then
    step "installing api dependencies into .venv"
    uv venv --python 3.12 "$VENV"
    VIRTUAL_ENV="$VENV" uv pip install -r "$ROOT/docker/requirements/api.txt"
    echo "$want" > "$STAMP"
  fi
}

# cwd must be apps/, not apps/api/: api/__init__.py makes "api" the importable
# package name and Python resolves it from the parent directory. alembic.ini's
# script_location (api/alembic) assumes the same cwd.
run_py() { (cd "$ROOT/apps" && PATH="$VENV/bin:$PATH" PYTHONPATH="$ROOT/apps" "$@"); }

migrate_and_seed() {
  step "applying migrations"
  run_py alembic -c api/alembic.ini upgrade head
  step "seeding admin account"
  run_py python -m api.scripts.seed
}

# ------------------------------------------------------------------ node env
ensure_node() {
  command -v pnpm >/dev/null || die "pnpm not found. Install: npm i -g pnpm@9"
  [ -d "$ROOT/node_modules" ] || { step "pnpm install"; (cd "$ROOT" && pnpm install); }
}

# ------------------------------------------------------------------ processes
PIDS=()
cleanup() {
  trap - INT TERM EXIT
  [ ${#PIDS[@]} -gt 0 ] && kill "${PIDS[@]}" 2>/dev/null || true
  wait 2>/dev/null || true
  echo
  echo "host processes stopped. Postgres/Redis are still up — 'make dev-down' to stop them."
}

start_api() {
  run_py uvicorn api.app.main:app --reload --port "${API_PORT:-8000}" &
  PIDS+=($!)
}
start_web() {
  (cd "$ROOT/apps/web" && PATH="$ROOT/node_modules/.bin:$PATH" pnpm dev --port "${WEB_PORT:-3000}") &
  PIDS+=($!)
}

banner() {
  cat <<EOF

  web    http://localhost:${WEB_PORT:-3000}   (login: ${ADMIN_EMAIL:-dev@agents.dev} / ${ADMIN_PASSWORD:-devdev})
  api    http://localhost:${API_PORT:-8000}/docs
  llm    ${LLM_BASE_URL}
  infer  ${INFER_BASE_URL}

  Ctrl-C stops api and web; the containers keep running.
EOF
}

case "$TARGET" in
  deps)
    start_deps; ensure_venv; migrate_and_seed
    step "deps ready — point your IDE at $VENV/bin/python, working dir $ROOT/apps"
    ;;
  api)
    ensure_venv
    trap cleanup INT TERM EXIT
    start_api; wait
    ;;
  web)
    ensure_node
    trap cleanup INT TERM EXIT
    start_web; wait
    ;;
  all)
    start_deps; ensure_venv; migrate_and_seed; ensure_node
    trap cleanup INT TERM EXIT
    step "starting api + web"
    start_api; start_web; banner; wait
    ;;
  *) die "unknown target '$TARGET' (expected: all | deps | api | web)" ;;
esac
