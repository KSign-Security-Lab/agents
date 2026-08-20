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

## Two things you can run

```
  make gpu                              make dev
  ────────                              ────────
  vllm    the LLM                       postgres + redis   in Docker
  infer   embed · rerank · ASR          api + web          on the host, reloading
     ▲                                     │
     └───── LLM_BASE_URL ──────────────────┘
            INFER_BASE_URL
```

That's the whole system. `make gpu` needs CUDA; `make dev` needs nothing but
Docker, so it runs on a Mac. They're independent — run one, the other, or both on
the same machine. There is no third mode.

### GPU side

Needs Docker with the NVIDIA container runtime, one GPU with ≥40GB free for the
default 32B AWQ model (two unlock `tp2`/`dp2`), and ~35GB of disk for weights.

```bash
make setup            # writes docker/.env
$EDITOR docker/.env   # VLLM_A_GPUS, and BIND_ADDR=0.0.0.0 if devs connect directly
make gpu              # or: make gpu MODE=tp2
```

Weights download on first start — ~30GB before the GPU is touched at all, which
is why `nvidia-smi` stays empty for a while. `make gpu-logs S=vllm-a` shows it;
`make pull-models` fetches them ahead of time.

#### "I set GPU 1, but it says GPU 0"

That's `CUDA_VISIBLE_DEVICES` doing its job. Setting `VLLM_A_GPUS=1` exposes
*only* physical GPU 1 to the process, and CUDA then renumbers it to index `0`.
So vLLM, torch and `nvidia-smi` **inside** the container all say `0`, no matter
which physical card it is. Logs can't tell you which GPU you got.

Two places can:

```bash
nvidia-smi                                    # on the host: which card holds the memory
docker inspect $(docker compose --env-file docker/.env \
  -f docker/compose.gpu.yml ps -q vllm-a) \
  -f '{{range .Config.Env}}{{println .}}{{end}}' | grep CUDA
```

If that disagrees with `docker/.env`, the container predates your edit. `docker
restart` never re-reads compose config; only a recreate does, which is what `make
gpu` does. Note also that `INFER_GPUS` defaults to `1` while `VLLM_A_GPUS`
defaults to `0`, so the two land on different cards unless you set both.

### Dev side

```bash
make dev-setup            # writes docker/.env.dev
$EDITOR docker/.env.dev   # set GPU_HOST — the only line in it
make dev
```

Starts Postgres and Redis, applies migrations, seeds the admin, then runs `api`
and `web` with reload and prints the URLs and login. Ctrl-C stops the two host
processes and leaves the containers up, so the next run takes seconds.

If the GPU box keeps its ports on loopback (the default — neither `vllm` nor
`infer` authenticates), tunnel to it and leave `GPU_HOST=localhost`:

```bash
ssh -N -L 8602:localhost:8602 -L 8603:localhost:8603 <gpu-host>
```

| | |
|---|---|
| `make dev S=deps` | containers only — then run `api` from your IDE against `.venv/bin/python`, working dir `apps/` |
| `make dev S=api` / `S=web` | one process, for two terminals |
| `INGEST=1 make dev` | also start the `worker` container (LibreOffice/OCR/ffmpeg — big image, only needed to debug ingest) |
| `make dev-down` / `dev-reset` | stop / stop and wipe the local database |
| `make dev-psql`, `make dev-logs S=worker` | poke at the containers |

[**docs/dev-topology.html**](docs/dev-topology.html) draws all of it — which
service sits where, the two env vars that cross between them, every port.

### Working on the code

Everything here runs against the dev side, using the `.venv` that `make dev`
creates:

```bash
make test           # 72 unit tests
make fmt            # ruff, config in apps/ruff.toml
make migrate        # make revision M="add x" to generate one
make eval           # answer + citation accuracy on the Korean gold set
make samples        # generate Korean test documents (uses the worker container)
make ingest FILE=samples/2026_공급계약서.pdf
make citation-check DOC=<uuid>
```

`make citation-check` is the important one: it draws every stored bounding box
onto the rendered page so you can *see* whether highlights land on the right
text. Numbers can be self-consistent and still point at the wrong line.

### Configuration is only what you decide

`docker/.env` holds ten values, `docker/.env.dev` holds one. Anything absent
falls back to a default that lives next to the code it affects:

| Where the default lives | What it covers |
|---|---|
| `docker/compose.gpu.yml` — `${VAR:-default}` | paths, ports, GPU indices, model ids, vLLM flags |
| `apps/api/app/config.py` — the `Settings` class | OCR, chunking, retrieval, agent and topic tuning, each beside the measurement that chose it |
| `scripts/dev.sh` | every dev URL, derived from `GPU_HOST` |

To override any `Settings` field, add it to the env file as `UPPER_CASE`.

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
apps/
  api/    FastAPI + agent + ingest pipeline   (app/agent/citations.py is the core protocol)
  infer/  GPU sidecar: embeddings, reranking, ASR
  web/    Next.js UI
docker/   compose.gpu.yml + compose.dev.yml, base.Dockerfile, .env examples
scripts/  dev.sh (owns the dev side), model download, sample generation
docs/     STATUS.md — what works, what remains; dev-topology.html — the two-machine dev split
```

`apps/*` is a pnpm workspace (`pnpm-workspace.yaml`, root `package.json`,
`turbo.json`) — today that's just `apps/web`, since `api`/`infer` are Python
and use `uv` directly against `docker/requirements/*.txt` rather than a
parallel `pyproject.toml`. There's no separate `worker/` directory: the
`worker` service is `apps/api/worker/`, built into a different image with a
different `CMD`, not a distinct codebase.
