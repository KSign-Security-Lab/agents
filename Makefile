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
        ingest eval fmt clean-images bootstrap

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n",$$1,$$2}'

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

probe-tools: ## Detect whether the served model supports native tool calls
	$(DC) exec -T api python -m api.scripts.probe_tools

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

clean-images: ## Reclaim docker build cache (safe: only rebuildable layers)
	docker builder prune -af
	@df -h / | tail -1
