# 문서 기반 에이전트 (Document QA Agent)

An internal agentic chatbot over your team's own documents.

Upload files → the agent parses and categorizes them → select documents (or a
folder) and open a chat session → the agent answers, and **every claim carries an
inline citation that opens the source at the exact page with the cited sentences
highlighted**.

That last part is the point of the system. An answer you cannot check is an
answer you cannot use, so the whole pipeline is built to preserve the geometry
needed to prove where each sentence came from.

---

## What it does

| | |
|---|---|
| **Verifiable citations** | Inline pills → hover for a preview → click to open the document at the cited page with a highlight over the supporting sentences. Recordings cite a timestamp and seek the player to it. |
| **File formats** | PDF (text and scanned), docx/xlsx/pptx, HWP/HWPX, images, plain text, audio and video |
| **Emergent topics** | The agent proposes Korean topic labels; near-duplicates merge automatically, and borderline pairs are surfaced for a human to decide |
| **Shared workspace** | One workspace, no private space. Anyone can post into any session, every message is attributed, and viewers watch answers stream in live |
| **Checkpoint + branch** | Revert to any point in a conversation. Nothing is deleted — the old path stays readable and switchable |
| **Agentic retrieval** | Query planning, parallel sub-agents per sub-question, hybrid dense+sparse search, cross-encoder reranking, whole-corpus escalation, table/cell citations |

Korean-first throughout: prompts, UI, sentence segmentation, OCR, and models.

---

## Architecture

```
  web (Next.js)          BFF proxy keeps the API token in an httpOnly cookie
    │
    ├─ api (FastAPI)     sessions, message tree, SSE streaming, citation resolution
    │    ├─ llm-gateway ──▶ vllm       one URL regardless of GPU topology
    │    ├─ infer ─────────▶ bge-m3 · bge-reranker-v2-m3 · faster-whisper
    │    └─ postgres        pgvector: dense vector + sparse lexical, HNSW
    │
    └─ worker (arq)      convert → extract → chunk → embed → categorize
         └─ LibreOffice + H2Orestart · PyMuPDF · Docling · OCR · ffmpeg
```

**Two design decisions carry most of the weight:**

1. **Everything visual becomes a PDF.** docx, HWP, images — all converted, so a
   single highlight path serves every format.
2. **`chunk_spans` maps chunk text offsets → page rectangles.** Without it a
   citation could only say "somewhere on page 14". With it, answer-time sentence
   alignment narrows the highlight to the sentences that actually supported the
   claim — at no extra LLM latency.

---

## Requirements

**Developing?** You don't need any of this locally — see
[Development](#development). One shared GPU server serves the model; your
machine runs Docker for Postgres/Redis and nothing else.

To run the full stack on one machine:

- Docker with the NVIDIA container runtime
- **1 GPU with ≥40GB free** for the default 32B AWQ model (two GPUs unlock
  `tp2`/`dp2` modes). Smaller models work on less — see `MODEL_ID`.
- ~35GB disk for model weights, plus room for Postgres and uploads
- Outbound network access on first run to download model weights

## Getting started

```bash
make setup          # writes docker/.env with generated secrets, creates data dirs
$EDITOR docker/.env # set DATA_ROOT / MODEL_DIR / VLLM_A_GPUS for this machine
make pull-models    # ~30GB, optional — vLLM otherwise downloads on first start
make build
make up
make migrate
make seed           # prints the generated admin password
```

Then open `http://localhost:8600` (or whatever `WEB_PORT` you set).

`make bootstrap` runs the whole sequence in order.

### Everything machine-specific is in `docker/.env`

| Variable | What it controls |
|---|---|
| `DATA_ROOT` | Postgres data, uploaded files, Redis, logs |
| `MODEL_DIR` / `INFER_MODEL_DIR` | Model weight caches |
| `WEB_PORT`, `API_PORT`, `LLM_GATEWAY_PORT`, `INFER_PORT`, `POSTGRES_PORT` | Host ports |
| `VLLM_A_GPUS`, `VLLM_B_GPUS`, `LLM_TP2_GPUS`, `INFER_GPUS` | CUDA device indices |
| `MODEL_ID` | The served model |

No paths, ports or device indices are hard-coded anywhere else.

### GPU topology

The application only ever knows `LLM_BASE_URL` (the gateway). How many replicas
sit behind it is deployment configuration:

```bash
make llm-mode MODE=single   # one replica          (1 GPU)
make llm-mode MODE=tp2      # model split in two   (2 GPUs, tensor parallel)
make llm-mode MODE=dp2      # load split in two    (2 GPUs, load balanced)
```

---

## Model choice

Default is `LGAI-EXAONE/EXAONE-4.0-32B-AWQ` for Korean quality.

> ⚠️ **EXAONE 4.0's licence (§3.1) prohibits commercial use of the model *and its
> output*,** naming revenue-generating products explicitly. It is appropriate for
> internal, non-revenue use only. For anything commercial, swap to an
> Apache-2.0 model — `MODEL_ID=Qwen/Qwen3-32B-AWQ` is a drop-in change and needs
> no other edits.

**Structured output uses forced tool calls, not guided JSON.** Measured on this
stack, guided JSON decoding produced structurally valid but semantically empty
results (`subqueries: [""]`) and the wrong intent, because constraining
whitespace pushes the model off its natural token path. Native tool calling
(Hermes format, which EXAONE's chat template emits) got every test case right.
Guided JSON remains the fallback for a model with no usable tool-call parser.

---

## Development

Two machines, and each runs only what it has to:

```
  GPU server                        your laptop
  ──────────                        ───────────
  vllm    (LLM)                     postgres  ┐ docker
  infer   (embed/rerank/ASR)        redis     ┘
     ▲                              api       ┐ host, hot reload
     └──── LLM_BASE_URL ────────────  web     ┘
           INFER_BASE_URL
```

Nothing local needs a GPU, so this works on a Mac. `api` and `web` are the two
things you edit, so they run on the host where the reloader and your debugger
can reach them.

[**docs/dev-topology.html**](docs/dev-topology.html) draws all of this — which
service sits on which machine, the two env vars that cross between them, and
every port. Open it in a browser if the split isn't obvious from the sketch above.

### On the GPU server, once

```bash
make setup                    # writes docker/.env with generated secrets
$EDITOR docker/.env           # set VLLM_A_GPUS, and BIND_ADDR=0.0.0.0 if devs connect directly
make build && make serve-gpu  # vllm + infer only — no Postgres, no web, no worker
```

### On each developer machine

```bash
make dev-setup                # writes docker/.env.dev
$EDITOR docker/.env.dev       # set GPU_HOST — usually the only line you change
make dev
```

`make dev` starts Postgres and Redis in Docker, applies migrations, seeds the
admin account, and runs `api` and `web` with reload. It prints the URLs and the
login. Ctrl-C stops the two host processes and leaves the containers up, so the
second run takes seconds.

If the GPU box keeps its ports on loopback (the default — neither `vllm` nor
`infer` authenticates), reach it with a tunnel and leave `GPU_HOST=localhost`:

```bash
ssh -N -L 8602:localhost:8602 -L 8603:localhost:8603 <gpu-host>
```

| | |
|---|---|
| `make dev` | everything (deps + migrate + seed + api + web) |
| `make dev S=deps` | only Postgres/Redis + migrate + seed — then run `api` from your IDE against `.venv/bin/python`, working dir `apps/` |
| `make dev S=api` / `S=web` | one process, for two terminals |
| `INGEST=1 make dev` | also start the `worker` container (LibreOffice/OCR/ffmpeg — big image, only needed to debug ingest) |
| `make dev-down` | stop the dev containers |
| `make dev-reset` | stop them and delete the local database |
| `make dev-psql`, `make dev-logs S=worker` | poke at the deps |

Config is one file, `docker/.env.dev`. The `LLM_MODE` / profile / nginx-gateway
machinery in `docker/compose.yml` is deployment concern only — a developer never
touches it.

### Checking the work

```bash
make samples        # generate Korean test documents
make ingest FILE=samples/2026_공급계약서.pdf
make test           # unit tests
make citation-check DOC=<uuid>   # render stored geometry onto pages as PNGs
```

`make citation-check` is the important one: it draws every stored bounding box
onto the rendered page so you can *see* whether highlights land on the right
text. Numbers can be self-consistent and still point at the wrong line.

Two host-side helpers exist for the GPU server, when Docker is in the way:
`scripts/dev_infer.sh` (the sidecar under `uvicorn --reload`) and
`scripts/dev_vllm.sh [GPU] [MODEL_ID]` (just `vllm serve`, no gateway, no DB —
for smoke-testing prompts or tool calling).

### Full stack on one machine

`make up` runs everything — Postgres, Redis, vllm, infer, api, worker, web — in
Docker on a single GPU host. That's the production-like path and what a deploy
uses; it is not the way to iterate on code.

## Layout

```
apps/
  api/    FastAPI + agent + ingest pipeline   (app/agent/citations.py is the core protocol)
  infer/  GPU sidecar: embeddings, reranking, ASR
  web/    Next.js UI
docker/   compose, Dockerfiles, .env.example
scripts/  dev.sh (local development), setup, sample generation
docs/     STATUS.md — what works, what remains; dev-topology.html — the two-machine dev split
```

`apps/*` is a pnpm workspace (`pnpm-workspace.yaml`, root `package.json`,
`turbo.json`) — today that's just `apps/web`, since `api`/`infer` are Python
and use `uv` directly against `docker/requirements/*.txt` rather than a
parallel `pyproject.toml`. There's no separate `worker/` directory: the
`worker` service is `apps/api/worker/`, built into a different image with a
different `CMD`, not a distinct codebase.
