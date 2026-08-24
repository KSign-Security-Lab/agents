# Design and roadmap

The decisions behind the build, and what is planned. Setup lives in the
[README](../README.md); current state in [STATUS.md](STATUS.md).

---

## Requirements this was built to

| Area | Decision |
|---|---|
| Citations | Inline pills → hover tooltip → click opens the source with a bounding-box highlight |
| Language | Korean primary |
| Architecture | Next.js UI/BFF + FastAPI agent service + arq workers |
| Workspace | One shared workspace, no private space. Anyone may post into any session; every message attributed |
| Sessions | Checkpoint revert that branches rather than deletes |
| Folders | Both a reusable document set *and* a project view listing its sessions |
| Categorization | Emergent — the agent proposes labels, near-duplicates merge |
| Formats | PDF (text + scanned), images, docx/xlsx/pptx, HWP/HWPX, text, audio, video |
| Agent | Planning, multi-hop, sub-agent fan-out, whole-corpus escalation, doc summaries, table reasoning |
| Scale | Small-team deployment: ~30 users, ~3k documents |
| Auth | Local email + password, roles admin/member |

---

## The citation protocol

The model is never trusted to produce references.

1. Retrieval hands the model a numbered passage list, `[S1] … [Sn]`, each labelled
   with its file, page and heading path.
2. The model writes `[S3]` inline immediately after any claim taken from passage 3.
3. A streaming parser extracts markers **as tokens arrive**, discards ids that
   were never offered, renumbers survivors into reader-facing pill numbers, and
   rewrites them as `[[cite:N]]` in stored content. Rejected ids are recorded to
   `agent_runs.rejected_citations`, making citation drift measurable.
4. After the stream ends, each citation is narrowed to the sentences that
   actually support the claim — by embedding similarity between the claim
   sentence and the chunk's sentences, so it costs no extra LLM call — and those
   sentence offsets are mapped to page rectangles through `chunk_spans`.

**Why the streaming parser is careful:** token boundaries fall wherever the
tokenizer likes, so a marker routinely arrives split (`"[S"` then `"12]"`). Any
tail that could still become a marker is held back rather than emitted. Tested
against every chunk size from 1 upward.

**The geometry contract:** rectangles are stored in PDF points with a *top-left*
origin, alongside the page width the extractor recorded. The browser scales by
`viewport.width / pageWidth`. Getting this wrong is the failure mode that
silently ruins the feature, which is why `pnpm bboxes` renders stored
boxes back onto pages for visual confirmation.

---

## Ingest

```
upload → convert → extract | transcribe → chunk → embed → categorize → ready
```

Each stage records its own state in `ingest_jobs`, so a failure resumes rather
than re-running a slow OCR or ASR pass.

- **Convert.** Everything visual becomes a PDF (LibreOffice for Office and HWP
  via the H2Orestart extension; images wrapped as single-page PDFs) so one
  highlight path serves every format. Audio and video skip this — their canonical
  form is a timestamped transcript, and a citation into them is a time range.
- **Extract.** PyMuPDF for pages with a text layer: exact line-level boxes, no
  model inference, and every converted Office/HWP file has a text layer. Direct
  OCR for pages without one. Both produce the same output: reading-ordered
  elements plus a line-granular span map.
- **Chunk.** Structure-aware, bounded by token budget, breaking at headings.
  Tables become their own chunk — splitting one would leave rows without their
  header. Element spans are rebased onto chunk-local offsets; losing that here
  would cap citation precision no matter what answer-time logic does.
- **Embed.** bge-m3 yields dense and sparse lexical weights in one pass. Using
  the model's own sparse weights instead of Postgres full-text search matters for
  Korean specifically: `to_tsvector` has no Korean stemmer, and Korean is
  agglutinative, so lexical matching on inflected forms would mostly fail.
- **Categorize.** Map-reduce summary → proposed labels → embedding match against
  existing topics.

---

## Retrieval and the agent

```
plan → researcher × sub-question (asyncio.gather) → merge → tables → compose → verify
```

Hand-rolled asyncio, not a LangGraph graph — `langgraph` is a dependency but
`AsyncPostgresSaver` isn't wired in; see Roadmap. `researcher` is
`retrieve → rerank → grade → (retry | escalate) → findings`. `tables` only
runs when the planner flagged `needs_tables` and a merged source is a table:
it offers `query_table`/`calc` to the model via a real multi-round
`tool_choice="auto"` loop and folds the result back in as a citable source.

- Hybrid dense + sparse, fused by Reciprocal Rank Fusion — no score calibration
  needed between two differently-scaled similarity measures
- Cross-encoder rerank, then interleave each sub-agent's best hits rather than
  taking a global top-N, so one sub-question cannot monopolise the context and
  leave a multi-part question half-answered
- Escalation to whole-corpus search when the selected documents come up short,
  with results flagged `out_of_scope` so the answer says where they came from

---

## Data model notes

- **`chunk_spans`** — chunk text offsets → page rectangle or time range. The
  table that makes precise highlighting possible.
- **`messages.parent_id`** — history is a tree. Reverting moves
  `sessions.active_leaf_id`; nothing is deleted, so an earlier path stays
  readable, which matters when the person who wrote it is not the person who
  reverted. `branch_root_id` labels the branch.
- **`citations`** — everything the UI needs is denormalized here, so rendering an
  old message never depends on re-running retrieval.

---

## Roadmap

**Done** — eval harness (`pnpm eval`, a Korean gold set derived from
`scripts/make_samples.py`), the verify/reflect node, table tools
(`query_table`/`calc` behind a real `tool_choice="auto"` loop), and an admin
UI for user management. See STATUS.md for what's implemented + unit-tested
versus actually exercised against a live model.

**Next**
1. Verify the two untested ingest paths on real files: HWP and a recording.
2. Run `pnpm eval` against a live stack and look at the actual pass/fail —
   it's never been exercised end to end, only checked for correctness offline.

**Then**
3. Web search as a registered tool once a provider is chosen (adapter is stubbed).

**Later**
4. Load testing on concurrent sessions; the per-session run lock and queue are
   implemented but untested under contention.
5. LangGraph `AsyncPostgresSaver` for resumable agent state, if interrupted runs
   become a real problem.
6. Object storage behind the existing `Storage` protocol, if single-node
   filesystem storage stops being enough.
