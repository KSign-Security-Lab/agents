# ---------------------------------------------------------------------------
#  Containers are plain docker compose — this file is only for what compose
#  can't do.
#
#      docker compose up -d      docker compose ps
#      docker compose down       docker compose logs -f vllm-a
#
#  What this machine runs is COMPOSE_PROFILES in .env. See .env.example.
# ---------------------------------------------------------------------------
SHELL := /bin/bash

# Anything touching the app goes through dev.sh, which owns one copy of the env
# derivation (DATABASE_URL from the .env ports, the .venv, cwd=apps/).
PY := bash scripts/dev.sh exec

.DEFAULT_GOAL := help
.PHONY: help dev migrate revision seed test fmt eval samples ingest citation-check pull-models

help: ## Show this help
	@echo "  Containers:  docker compose up -d | down | ps | logs -f <service>"
	@echo "               what runs is COMPOSE_PROFILES in .env"
	@echo
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n",$$1,$$2}'

dev: ## Postgres+Redis, migrate, seed, then api and web on the host with reload
	bash scripts/dev.sh $(S)

## ------------------------------------------------- against the dev database
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

## ------------------------------------- need LibreOffice/OCR: the worker image
samples: ## Generate Korean sample documents
	docker compose run --rm --no-deps -v $(CURDIR):/work -w /work \
	  worker python scripts/make_samples.py samples

ingest: ## Ingest a local file: make ingest FILE=samples/x.pdf
	@test -n "$(FILE)" || { echo "usage: make ingest FILE=path"; exit 1; }
	docker compose exec -T worker python -m api.scripts.ingest_local "/storage/../$(FILE)"

citation-check: ## Draw stored bboxes onto the pages: make citation-check DOC=<uuid>
	@test -n "$(DOC)" || { echo "usage: make citation-check DOC=<document-id>"; exit 1; }
	docker compose exec -T worker python -m api.scripts.citation_check $(DOC)
	@echo "==> wrote data/out/ — open the PNGs and check the boxes cover the right text"

pull-models: ## Pre-download LLM + embed/rerank/ASR weights (~30GB)
	bash scripts/pull_models.sh
