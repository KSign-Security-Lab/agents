# ---------------------------------------------------------------------------
#  Agentic Document QA Platform
#
#  LLM_MODE selects the GPU topology and is mapped onto a compose profile.
#  The application always talks to the gateway, never to a replica.
#    make up                  # LLM_MODE from docker/.env (default: single)
#    make up LLM_MODE=dp2     # two replicas, load split across both GPUs
#    make llm-mode MODE=tp2   # restart serving with the model split over 2 GPUs
# ---------------------------------------------------------------------------
SHELL := /bin/bash
ENV_FILE := docker/.env
COMPOSE_FILE := docker/compose.yml

# Read LLM_MODE from the env file unless overridden on the command line.
LLM_MODE ?= $(shell grep -E '^LLM_MODE=' $(ENV_FILE) 2>/dev/null | cut -d= -f2)
LLM_MODE := $(if $(LLM_MODE),$(LLM_MODE),single)
PROFILE := llm-$(LLM_MODE)

DC := docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE)
DCP := LLM_MODE=$(LLM_MODE) $(DC) --profile $(PROFILE)

.DEFAULT_GOAL := help
.PHONY: help setup up down restart ps logs build build-api build-worker build-infer \
        build-web migrate revision seed shell-api shell-worker psql redis-cli \
        llm-mode pull-models samples test test-citations citation-check \
        ingest eval fmt clean-images bootstrap \
        dev dev-setup dev-build dev-down dev-reset dev-logs dev-psql serve-gpu

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n",$$1,$$2}'

## --------------------------------------------------------------- dev
#  Local development: GPU work stays on the GPU server, Postgres/Redis run in
#  Docker here, api and web run on the host with hot reload. See scripts/dev.sh.
DEV_ENV := docker/.env.dev
DCDEV := docker compose -f docker/compose.dev.yml

dev-setup: ## Create docker/.env.dev from the example, then edit GPU_HOST
	@test -f $(DEV_ENV) && echo "$(DEV_ENV) already exists, leaving it alone" || { \
	  cp docker/.env.dev.example $(DEV_ENV); \
	  echo "wrote $(DEV_ENV) -- set GPU_HOST, then run 'make dev'"; }

dev: ## Start local dev: deps + migrate + seed + api and web with reload
	bash scripts/dev.sh $(S)

dev-build: ## Rebuild the dev worker image (only needed after a requirements/Dockerfile change)
	INGEST=1 $(DCDEV) --profile ingest build worker
	INGEST=1 $(DCDEV) --profile ingest up -d --wait

dev-down: ## Stop the local dev containers (data preserved)
	$(DCDEV) --profile ingest down

dev-reset: ## Stop dev containers and delete the local dev database
	$(DCDEV) --profile ingest down -v
	rm -rf docker/devdata

dev-logs: ## Tail dev container logs, e.g. make dev-logs S=worker
	$(DCDEV) --profile ingest logs -f --tail=200 $(S)

dev-psql: ## psql into the local dev database
	$(DCDEV) exec postgres psql -U agents -d agents

serve-gpu: ## On the GPU server: serve ONLY vllm + infer for remote developers
	$(DCP) up -d llm-gateway infer $(if $(filter dp2,$(LLM_MODE)),vllm-a vllm-b,$(if $(filter tp2,$(LLM_MODE)),vllm-tp,vllm-a))
	@$(DCP) ps

## ------------------------------------------------------------- setup
setup: ## First run on a new machine: create .env with fresh secrets and data dirs
	@test -f $(ENV_FILE) && echo "$(ENV_FILE) already exists, leaving it alone" || { \
	  cp docker/.env.example $(ENV_FILE); \
	  python3 scripts/gen_secrets.py $(ENV_FILE); \
	  echo "wrote $(ENV_FILE) with generated secrets"; }
	@set -a; . $(ENV_FILE); set +a; \
	  mkdir -p "$$DATA_ROOT"/{postgres,redis,storage,logs,out} "$$MODEL_DIR" "$$INFER_MODEL_DIR"; \
	  echo "created data directories under $$DATA_ROOT"
	@echo
	@echo "Next:  make pull-models   (downloads ~30GB; optional, vLLM will fetch on first start)"
	@echo "       make build && make up"
	@echo "       make migrate && make seed"

bootstrap: setup build up migrate seed ## setup + build + up + migrate + seed, in order
	@echo "==> ready. web on port $$(grep -E '^WEB_PORT=' $(ENV_FILE) | cut -d= -f2)"

## ------------------------------------------------------------- lifecycle
up: ## Start the whole stack (LLM_MODE selects the GPU topology)
	@echo "==> LLM_MODE=$(LLM_MODE) (profile $(PROFILE))"
	$(DCP) up -d
	@$(MAKE) --no-print-directory ps

down: ## Stop the stack (data on /data is preserved)
	$(DC) --profile llm-single --profile llm-dp2 --profile llm-tp2 down

restart: down up ## Restart everything

ps: ## Show service status
	@$(DCP) ps

logs: ## Tail logs, e.g. make logs S=api
	$(DCP) logs -f --tail=200 $(S)

build: ## Build all application images
	$(DC) build api worker infer web

build-api:    ; $(DC) build api
build-worker: ; $(DC) build worker
build-infer:  ; $(DC) build infer
build-web:    ; $(DC) build web

## ------------------------------------------------------------- llm serving
llm-mode: ## Switch GPU topology without touching app code: make llm-mode MODE=dp2
	@test -n "$(MODE)" || { echo "usage: make llm-mode MODE=single|tp2|dp2"; exit 1; }
	@case "$(MODE)" in single|tp2|dp2) ;; *) echo "MODE must be single|tp2|dp2"; exit 1;; esac
	$(DC) --profile llm-single --profile llm-dp2 --profile llm-tp2 stop vllm-a vllm-b vllm-tp || true
	$(DC) --profile llm-single --profile llm-dp2 --profile llm-tp2 rm -f vllm-a vllm-b vllm-tp || true
	sed -i 's/^LLM_MODE=.*/LLM_MODE=$(MODE)/' $(ENV_FILE)
	@$(MAKE) --no-print-directory up LLM_MODE=$(MODE)
	@echo "==> gateway now reports:"; sleep 3; curl -s localhost:$$(grep -E '^LLM_GATEWAY_PORT=' $(ENV_FILE) | cut -d= -f2)/gateway/health; echo

pull-models: ## Pre-download LLM + embed/rerank/ASR weights to /data
	bash scripts/pull_models.sh

## ------------------------------------------------------------- database
migrate: ## Apply migrations
	$(DC) exec -T api alembic -c api/alembic.ini upgrade head

revision: ## Autogenerate a migration: make revision M="add x"
	$(DC) exec -T api alembic -c api/alembic.ini revision --autogenerate -m "$(M)"

seed: ## Create the first admin user and demo folder
	$(DC) exec -T api python -m api.scripts.seed

psql: ## Open a psql shell
	$(DC) exec postgres psql -U $$(grep -E '^POSTGRES_USER=' $(ENV_FILE) | cut -d= -f2) \
	                          -d $$(grep -E '^POSTGRES_DB=' $(ENV_FILE) | cut -d= -f2)

redis-cli: ; $(DC) exec redis redis-cli

## ------------------------------------------------------------- dev / test
shell-api:    ; $(DC) exec api bash
shell-worker: ; $(DC) exec worker bash

test: ## Run the API test suite
	$(DC) exec -T api pytest api/tests -q

test-citations: ## Citation marker parsing + span->bbox geometry
	$(DC) exec -T api pytest api/tests/test_citations.py -q

citation-check: ## Render a document's stored bboxes onto its pages: make citation-check DOC=<uuid>
	@test -n "$(DOC)" || { echo "usage: make citation-check DOC=<document-id>"; exit 1; }
	$(DC) exec -T worker python -m api.scripts.citation_check $(DOC)
	@echo "==> wrote $$(grep -E '^DATA_ROOT=' $(ENV_FILE) | cut -d= -f2)/out/ — open the PNGs and check the boxes cover the right text"

samples: ## Generate Korean sample documents for testing
	$(DC) run --rm --no-deps -v $$(pwd):/work -w /work worker python scripts/make_samples.py samples

ingest: ## Ingest a local file directly: make ingest FILE=samples/x.pdf
	@test -n "$(FILE)" || { echo "usage: make ingest FILE=path"; exit 1; }
	$(DC) exec -T worker python -m api.scripts.ingest_local "/storage/../$(FILE)"

eval: ## Answer + citation accuracy on the Korean gold set
	$(DC) exec -T api python -m api.scripts.run_eval

fmt: ## Format and lint
	$(DC) exec -T api sh -c "ruff check --fix api && ruff format api"

clean-images: ## Reclaim build cache and images orphaned by a rebuild (safe: only rebuildable layers)
	docker builder prune -af
	docker image prune -f          # untagged only; never a tagged image

	@df -h / | tail -1
