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

```bash
make samples        # generate Korean test documents
make ingest FILE=samples/2026_공급계약서.pdf
make test           # unit tests
make citation-check DOC=<uuid>   # render stored geometry onto pages as PNGs
make logs S=worker
make psql
```

`make citation-check` is the important one: it draws every stored bounding box
onto the rendered page so you can *see* whether highlights land on the right
text. Numbers can be self-consistent and still point at the wrong line.

## Layout

```
api/      FastAPI + agent + ingest pipeline   (app/agent/citations.py is the core protocol)
infer/    GPU sidecar: embeddings, reranking, ASR
web/      Next.js UI
docker/   compose, Dockerfiles, .env.example
scripts/  setup and sample generation
docs/     STATUS.md — what works, what remains
```
