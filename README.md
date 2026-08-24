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

## Running it

One `compose.yaml`, one `.env`, and plain compose commands:

```bash
cp .env.example .env
$EDITOR .env             # set COMPOSE_PROFILES — that's what this machine runs
docker compose up -d
```

`COMPOSE_PROFILES` is the only structural decision, and compose reads it straight
out of `.env`, so there are no `-f` or `--profile` flags to remember:

| `COMPOSE_PROFILES=` | Starts | For |
|---|---|---|
| `gpu,llm-single` | llm-gateway, infer, vllm-a | a GPU server, one replica |
| `gpu,llm-tp2` | llm-gateway, infer, vllm-tp | one model split across two GPUs |
| `gpu,llm-dp2` | llm-gateway, infer, vllm-a, vllm-b | two replicas, load split |
| `dev` | postgres, redis | a developer's machine |
| `dev,ingest` | ...plus worker | debugging ingest |
| `gpu,llm-single,dev` | all of the above | both roles on one box |

Everything else is normal compose — `docker compose down`, `ps`,
`logs -f vllm-a`, `exec postgres psql -U agents`, `restart infer`.

```
  gpu profile                           dev profile
  ───────────                           ───────────
  vllm    the LLM                       postgres + redis   containers
  infer   embed · rerank · ASR          api + web          host, reloading
     ▲                                     │
     └───── LLM_BASE_URL ──────────────────┘
            INFER_BASE_URL
```

`api` and `web` are deliberately **not** in compose: they're what you edit, so
they run on the host under a reloader, which is what `make dev` is for.

### On a GPU server

Needs Docker with the NVIDIA container runtime, one GPU with ≥40GB free for the
default 32B AWQ model, and ~35GB of disk for weights.

```bash
docker compose up -d
docker compose logs -f vllm-a
```

Weights download on first start — ~30GB before the GPU is touched at all, which
is why `nvidia-smi` stays empty for a while. `make pull-models` fetches them
ahead of time.

#### "I set GPU 1, but it says GPU 0"

That's `CUDA_VISIBLE_DEVICES` doing its job. Setting `VLLM_A_GPUS=1` exposes
*only* physical GPU 1 to the process, and CUDA renumbers it to index `0`. So
vLLM, torch and `nvidia-smi` **inside** the container all say `0` whichever
physical card it is — the logs cannot tell you which GPU you got.

```bash
nvidia-smi                                             # host: which card holds the memory
docker compose exec vllm-a printenv CUDA_VISIBLE_DEVICES
```

If that disagrees with `.env`, the container predates your edit — `docker
restart` never re-reads compose config, only `up -d` recreates. Note also that
`INFER_GPUS` defaults to `1` while `VLLM_A_GPUS` defaults to `0`, so the two land
on different cards unless you set both.

### On a developer's machine

```bash
cp .env.example .env      # ships with COMPOSE_PROFILES=dev
$EDITOR .env              # set GPU_HOST
make dev
```

`make dev` starts postgres and redis, applies migrations, seeds the admin, then
runs `api` and `web` with reload and prints the URLs and login. Ctrl-C stops the
two host processes and leaves the containers up, so the next run takes seconds.

If the GPU box keeps its ports on loopback (the default — neither `vllm` nor
`infer` authenticates), tunnel to it and leave `GPU_HOST=localhost`:

```bash
ssh -N -L 8602:localhost:8602 -L 8603:localhost:8603 <gpu-host>
```

| | |
|---|---|
| `make dev S=deps` | containers only — then run `api` from your IDE against `.venv/bin/python`, working dir `apps/` |
| `make dev S=api` / `S=web` | one process, for two terminals |
| `INGEST=1 make dev` | also start the `worker` container (LibreOffice/OCR/ffmpeg — big image, only for ingest work) |

[**docs/dev-topology.html**](docs/dev-topology.html) draws all of it — what each
service is, which profile starts it, and every port.

### Working on the code

Against the dev containers, using the `.venv` that `make dev` creates:

```bash
make test           # 72 unit tests
make fmt            # ruff, config in apps/ruff.toml
make migrate        # make revision M="add x" to generate one
make eval           # answer + citation accuracy on the Korean gold set
make samples        # generate Korean test documents (uses the worker image)
make ingest FILE=samples/2026_공급계약서.pdf
make citation-check DOC=<uuid>
```

`make citation-check` is the important one: it draws every stored bounding box
onto the rendered page so you can *see* whether highlights land on the right
text. Numbers can be self-consistent and still point at the wrong line.

### Configuration is only what you decide

`.env` holds `COMPOSE_PROFILES` plus a handful of values. Anything absent falls
back to a default that lives next to the code it affects:

| Where the default lives | What it covers |
|---|---|
| `compose.yaml` — `${VAR:-default}` | paths, ports, GPU indices, model ids, vLLM flags |
| `apps/api/app/config.py` — the `Settings` class | OCR, chunking, retrieval, agent and topic tuning, each beside the measurement that chose it |
| `scripts/dev.sh` | every dev URL, derived from `GPU_HOST` |

To override any `Settings` field, add it to `.env` as `UPPER_CASE`.

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

## Layout

```
compose.yaml   every container, selected by COMPOSE_PROFILES in .env
apps/
  api/    FastAPI + agent + ingest pipeline   (app/agent/citations.py is the core protocol)
  infer/  GPU sidecar: embeddings, reranking, ASR
  web/    Next.js UI
docker/   base.Dockerfile, nginx gateway template, requirements
scripts/  dev.sh (the host processes + code tasks), model download, samples
docs/     STATUS.md — what works, what remains; dev-topology.html — the two-machine dev split
```

`apps/*` is a pnpm workspace (`pnpm-workspace.yaml`, root `package.json`,
`turbo.json`) — today that's just `apps/web`, since `api`/`infer` are Python
and use `uv` directly against `docker/requirements/*.txt` rather than a
parallel `pyproject.toml`. There's no separate `worker/` directory: the
`worker` service is `apps/api/worker/`, built into a different image with a
different `CMD`, not a distinct codebase.
