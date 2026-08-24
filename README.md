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
    │    ├─ vllm ──────────▶ k8s Service, one URL however many GPU machines
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

Two kinds of machine, sharing nothing but a network.

### GPU machines — a k3s cluster

They hold no configuration of their own. Join the cluster and they're done.

```bash
sudo k8s/setup.sh server                       # the first machine
sudo k8s/setup.sh agent <server-ip> <token>    # each one after it
kubectl apply -k k8s/
```

`k8s/setup.sh` checks the driver and toolkit, installs k3s and the NVIDIA device
plugin, and finishes by running a real GPU pod — if that passes, the cluster can
serve models. See [`k8s/README.md`](k8s/README.md) for the detail, including the
RuntimeClass trap and how to get `agents/infer:dev` onto the nodes.

**GPU layout is two numbers**, both in `k8s/vllm.yaml`:

```yaml
replicas: 1                  # how many copies of the model
nvidia.com/gpu: 1            # how many whole cards each copy gets
```

vLLM counts the cards it was given and splits the model across all of them, so
there is no second setting to keep in step and no card index to write anywhere.
Three cards is `replicas: 3, gpu: 1` for throughput, or `replicas: 1, gpu: 2` for
a bigger model with the third card left for `infer`.

Kubernetes allocates whole cards and never gives two pods the same one; for
extended resources it forces requests to equal limits, so you cannot overcommit
by accident.

### A developer's machine — two lines of `.env`

```bash
cp .env.example .env
$EDITOR .env              # COMPOSE_PROFILES=dev, GPU_HOST=<any cluster node>
docker compose up -d      # postgres + redis
pnpm install && pnpm venv
pnpm dev                  # api + web on the host, reloading
```

`GPU_HOST` is *any* node's IP. The models sit behind Kubernetes Services on fixed
NodePorts — 30862 for vllm, 30863 for infer — each routing to whichever pod is
ready, so a developer never learns how many GPU machines exist or which answered.

`pnpm dev` migrates, seeds `dev@agents.dev / devdev`, then runs both host
processes. Ctrl-C stops them and leaves the containers up.

| | |
|---|---|
| `pnpm up` | just the containers, for running `api` from an IDE against `.venv/bin/python`, working dir `apps/` |
| `pnpm api` / `pnpm web` | one process, for two terminals |
| `COMPOSE_PROFILES=dev,ingest` | also start the `worker` container — a big image, only needed for ingest work |

### Adding GPU capacity

| You want | Do this |
|---|---|
| more copies on machines you have | `kubectl -n agents scale deploy/vllm --replicas=3` |
| a whole new machine | `sudo k8s/setup.sh agent <ip> <token>`, then scale |
| a model too big for one card | raise `nvidia.com/gpu` |

Adding a machine changes no configuration anywhere: it registers itself, the
device plugin advertises its cards, and the Service adds the pod once it passes
readiness. No `.env` edit on any laptop, nothing restarted.

[**docs/dev-topology.html**](docs/dev-topology.html) draws all of it.

### Stopping, restarting, starting over

```bash
docker compose ps                 # what's up
docker compose restart redis      # bounce one service, same config
docker compose up -d              # recreate whatever changed in .env
docker compose down               # remove the containers; data survives
```

`restart` does **not** re-read `.env` — only `up -d` recreates a container, which
is what picks up an edit. And because Postgres and Redis are bind mounts under
`data/` rather than named volumes, `down -v` does *not* delete them. To start from
an empty database:

```bash
docker compose down && rm -rf data/postgres && docker compose up -d && pnpm migrate && pnpm seed
```

### Working on the code

Against the dev containers, using the `.venv` that `pnpm venv` builds. `pnpm run`
prints this list from `package.json`, so it can't go stale:

```bash
pnpm test                     # 72 unit tests
pnpm lint                     # ruff, config in apps/ruff.toml
pnpm migrate                  # pnpm revision "add x" to generate one
pnpm eval                     # answer + citation accuracy on the Korean gold set
pnpm samples                  # Korean test documents (uses the worker image)
pnpm ingest samples/2026_공급계약서.pdf
pnpm bboxes <document-id>
```

`pnpm bboxes` is the important one: it draws every stored bounding box
onto the rendered page so you can *see* whether highlights land on the right
text. Numbers can be self-consistent and still point at the wrong line.

### Configuration is only what you decide

| Where it lives | What it covers |
|---|---|
| `.env` — two values | `COMPOSE_PROFILES`, `GPU_HOST` |
| `k8s/vllm.yaml` | `replicas` and `nvidia.com/gpu` — the entire GPU layout |
| `k8s/config.yaml` | model ids, context length, VRAM share, embed/rerank/ASR models |
| `compose.*.yml` — `${VAR:-default}` | the dev containers' ports and paths |
| `apps/api/app/config.py` — `Settings` | OCR, chunking, retrieval, agent and topic tuning, each beside the measurement that chose it |
| `package.json` — `scripts` | every command; `pnpm run` prints it |

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
compose.yaml       includes the three below; COMPOSE_PROFILES in .env picks what runs
compose.postgres.yml  postgres    compose.worker.yml  worker
compose.redis.yml     redis
k8s/               the GPU side: setup.sh, vllm, infer — see k8s/README.md
package.json   every task — pnpm run
apps/
  api/    FastAPI + agent + ingest pipeline   (app/agent/citations.py is the core protocol)
  infer/  GPU sidecar: embeddings, reranking, ASR
  web/    Next.js UI
docker/   base.Dockerfile, requirements
scripts/  make_samples.py, run inside the worker image
docs/     STATUS.md — what works, what remains; dev-topology.html — the two-machine dev split
```

`apps/*` is a pnpm workspace (`pnpm-workspace.yaml`, root `package.json`,
`turbo.json`) — today that's just `apps/web`, since `api`/`infer` are Python
and use `uv` directly against `docker/requirements/*.txt` rather than a
parallel `pyproject.toml`. There's no separate `worker/` directory: the
`worker` service is `apps/api/worker/`, built into a different image with a
different `CMD`, not a distinct codebase.
