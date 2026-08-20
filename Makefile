# ---------------------------------------------------------------------------
#  문서 기반 에이전트 — two things you can run, independently.
#
#    make gpu    the GPU side: vllm + infer          (docker/compose.gpu.yml)
#    make dev    the dev side: db, queue, api, web   (docker/compose.dev.yml)
#
#  A developer runs `make dev` and points GPU_HOST at a machine running
#  `make gpu`. Want everything on one box? Run both. There is no third mode.
# ---------------------------------------------------------------------------
SHELL := /bin/bash

GPU_ENV  := docker/.env
DEV_ENV  := docker/.env.dev
DCGPU    := docker compose --env-file $(GPU_ENV) -f docker/compose.gpu.yml
DCDEV    := docker compose -f docker/compose.dev.yml

# MODE picks the GPU topology; it persists to docker/.env so `make gpu` repeats it.
MODE ?= $(shell grep -E '^LLM_MODE=' $(GPU_ENV) 2>/dev/null | cut -d= -f2)
MODE := $(if $(MODE),$(MODE),single)
DCGPUP := LLM_MODE=$(MODE) $(DCGPU) --profile llm-$(MODE)

# Anything touching the app runs through dev.sh, which owns one copy of the env
# derivation (DATABASE_URL from the dev ports, .venv, cwd=apps/).
PY := bash scripts/dev.sh exec

.DEFAULT_GOAL := help
.PHONY: help gpu gpu-down gpu-logs gpu-build gpu-health pull-models setup \
        dev dev-setup dev-down dev-reset dev-logs dev-psql dev-build \
        migrate revision seed test fmt samples ingest eval citation-check clean

help: ## Show this help
	@echo "  GPU side"
	@grep -hE '^(gpu|pull-models|setup)[a-z-]*:.*?## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "    \033[36m%-16s\033[0m %s\n",$$1,$$2}'
	@echo "  Dev side"
	@grep -hE '^dev[a-z-]*:.*?## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "    \033[36m%-16s\033[0m %s\n",$$1,$$2}'
	@echo "  Working on the code"
	@grep -hE '^(migrate|revision|seed|test|fmt|samples|ingest|eval|citation-check|clean):.*?## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "    \033[36m%-16s\033[0m %s\n",$$1,$$2}'

## =========================================================== GPU side
setup: ## GPU side, first run: create docker/.env and its data dirs
	@test -f $(GPU_ENV) && echo "$(GPU_ENV) already exists, leaving it alone" || { \
	  cp docker/.env.example $(GPU_ENV); \
	  echo "wrote $(GPU_ENV) -- check VLLM_A_GPUS and BIND_ADDR"; }
	@set -a; . $(GPU_ENV); set +a; \
	  mkdir -p "$${GPU_DATA_ROOT:-docker/gpudata}" "$${MODEL_DIR:-docker/gpudata/models/vllm}" \
	           "$${INFER_MODEL_DIR:-docker/gpudata/models/infer}"

gpu: ## Serve the models. make gpu MODE=single|tp2|dp2 (default: whatever .env says)
	@test -f $(GPU_ENV) || { echo "run 'make setup' first"; exit 1; }
	@case "$(MODE)" in single|tp2|dp2) ;; *) echo "MODE must be single|tp2|dp2"; exit 1;; esac
	@grep -q '^LLM_MODE=$(MODE)$$' $(GPU_ENV) || \
	  { sed -i.bak 's/^LLM_MODE=.*/LLM_MODE=$(MODE)/' $(GPU_ENV) && rm -f $(GPU_ENV).bak; }
	$(DCGPU) --profile llm-single --profile llm-tp2 --profile llm-dp2 \
	  rm -sf vllm-a vllm-b vllm-tp 2>/dev/null || true
	$(DCGPUP) up -d
	@$(DCGPUP) ps
	@echo "==> weights download on first start; watch it with 'make gpu-logs'"

gpu-down: ## Stop the GPU side
	$(DCGPU) --profile llm-single --profile llm-tp2 --profile llm-dp2 down

gpu-build: ## Rebuild the infer image (after apps/infer or its requirements change)
	$(DCGPU) build infer

gpu-logs: ## Tail GPU-side logs, e.g. make gpu-logs S=vllm-a
	$(DCGPUP) logs -f --tail=200 $(S)

gpu-health: ## Is the model actually being served?
	@set -a; . $(GPU_ENV); set +a; \
	  echo "gateway: $$(curl -s localhost:$${LLM_GATEWAY_PORT:-8602}/gateway/health)"; \
	  echo "models:  $$(curl -s localhost:$${LLM_GATEWAY_PORT:-8602}/v1/models)"; \
	  echo "infer:   $$(curl -s localhost:$${INFER_PORT:-8603}/health)"

pull-models: ## Pre-download LLM + embed/rerank/ASR weights (~30GB)
	bash scripts/pull_models.sh

## =========================================================== Dev side
dev-setup: ## Dev side, first run: create docker/.env.dev
	@test -f $(DEV_ENV) && echo "$(DEV_ENV) already exists, leaving it alone" || { \
	  cp docker/.env.dev.example $(DEV_ENV); \
	  echo "wrote $(DEV_ENV) -- set GPU_HOST, then run 'make dev'"; }

dev: ## Start dev: db + queue in Docker, api + web on the host with reload
	bash scripts/dev.sh $(S)

dev-down: ## Stop the dev containers (data preserved)
	$(DCDEV) --profile ingest down

dev-reset: ## Stop them and delete the local database
	$(DCDEV) --profile ingest down -v
	rm -rf docker/devdata

dev-build: ## Rebuild the worker image (after a requirements/Dockerfile change)
	$(DCDEV) --profile ingest build worker
	$(DCDEV) --profile ingest up -d --wait

dev-logs: ## Tail dev container logs, e.g. make dev-logs S=worker
	$(DCDEV) --profile ingest logs -f --tail=200 $(S)

dev-psql: ## psql into the local database
	$(DCDEV) exec postgres psql -U agents -d agents

## ================================================== Working on the code
#  These run against the dev side. `make dev` (or `make dev S=deps`) creates the
#  .venv they use; anything needing LibreOffice or OCR goes to the worker
#  container instead, because those are why that image is large.
migrate: ## Apply migrations
	@$(PY) alembic -c api/alembic.ini upgrade head

revision: ## Autogenerate a migration: make revision M="add x"
	@test -n "$(M)" || { echo 'usage: make revision M="add x"'; exit 1; }
	@$(PY) alembic -c api/alembic.ini revision --autogenerate -m "$(M)"

seed: ## Create/reset the admin account
	@$(PY) python -m api.scripts.seed

test: ## Run the API test suite
	@$(PY) pytest api/tests -q

# `ruff format` is deliberately not wired in: it would rewrite ~3800 lines in one
# go, which deserves its own commit rather than riding along with a lint fix.
fmt: ## Lint and autofix, config in apps/ruff.toml
	@$(PY) ruff check --fix api

eval: ## Answer + citation accuracy on the Korean gold set
	@$(PY) python -m api.scripts.run_eval

samples: ## Generate Korean sample documents
	$(DCDEV) --profile ingest run --rm --no-deps -v $(CURDIR):/work -w /work \
	  worker python scripts/make_samples.py samples

ingest: ## Ingest a local file: make ingest FILE=samples/x.pdf
	@test -n "$(FILE)" || { echo "usage: make ingest FILE=path"; exit 1; }
	$(DCDEV) --profile ingest exec -T worker python -m api.scripts.ingest_local "/storage/../$(FILE)"

citation-check: ## Draw stored bboxes onto the pages: make citation-check DOC=<uuid>
	@test -n "$(DOC)" || { echo "usage: make citation-check DOC=<document-id>"; exit 1; }
	$(DCDEV) --profile ingest exec -T worker python -m api.scripts.citation_check $(DOC)
	@echo "==> wrote docker/devdata/out/ — open the PNGs and check the boxes cover the right text"

clean: ## Reclaim build cache and images orphaned by a rebuild
	docker builder prune -af
	docker image prune -f          # untagged only; never a tagged image
	@df -h / | tail -1
