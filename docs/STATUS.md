# Status

Where the build stands, what has been verified against real data, and what is
left. Written to be picked up on a different machine.

---

## Verified working

Each of these was exercised end to end against a live stack with real Korean
documents and real models — not mocked.

### Infrastructure
- Compose stack: Postgres 17 + pgvector, Redis, nginx LLM gateway, vLLM, `infer`
  sidecar, API, arq worker, Next.js web
- Alembic migrations create 18 tables from scratch, including HNSW indexes on
  both the dense `vector` and sparse `sparsevec` columns
- `LLM_MODE=single|tp2|dp2` all render valid nginx config; `single`/`tp2` start
  even while a replica is still loading (request-time DNS resolution)
- Repo restructured into an `apps/` pnpm+uv monorepo (`apps/api`, `apps/web`,
  `apps/infer`); `docker compose build` confirmed for `api`/`worker`/`web` —
  each built image's container actually boots (`import api.app.main` inside
  the container, an HTTP 200 from the built `web` image) rather than just
  parsing. `infer`'s build was not re-run (its base image is a 26GB pull not
  worth repeating for the same COPY mechanism already proven twice), but its
  non-GPU modules were import-checked directly from the new path.

### Serving
- vLLM serving EXAONE-4.0-32B-AWQ, 32k context
- Native Hermes tool calling verified working for structured decisions
- `infer` sidecar: bge-m3 (dense 1024-d + sparse lexical in one pass),
  bge-reranker-v2-m3, faster-whisper — all lazy-loaded on one GPU alongside vLLM

### Retrieval
- Hybrid dense + sparse fused with RRF; both HNSW indexes confirmed used by the
  query planner via `EXPLAIN`
- Document scoping verified not to leak across documents
- Reranker decisive on a Korean test set (0.85 for the correct passage vs 0.0015
  for the next)

### Ingest
Ran end to end for PDF, docx, odt, plain text, image, and scanned PDF:

| Format | Route | Result |
|---|---|---|
| PDF (text) | PyMuPDF | 8 headings, 1 table with 16/16 cell boxes, per-line spans |
| docx / odt | LibreOffice → PDF → PyMuPDF | Korean preserved, structure intact |
| Scanned PDF / image | OCR → line boxes | all 13 text regions recovered, correct reading order |
| txt | LibreOffice → PDF | fine |

- Emergent taxonomy working: 5 topics from 4 documents, "계약" correctly folded
  into "계약/법무" with the alias kept for audit
- Korean summaries, key entities and suggested questions generated per document

### The citation feature (the point of the system)
- Full agent turn: plan → 4 parallel sub-agents → merge → answer with inline
  citations, ~13s
- **Highlights visually confirmed** to land on the correct text — verified by
  rendering stored geometry back onto the page (`make citation-check`), for both
  native-text PDFs and OCR'd scans
- Sentence-level narrowing works: within a two-line paragraph only the line
  containing the answer is highlighted
- Table citations highlight the table; cell boxes are stored for finer use
- Zero hallucinated citations across all test runs (invalid ids are stripped and
  recorded to `agent_runs.rejected_citations`)

### HTTP + UI layer
- Auth, upload → queue → worker → ingest, folders, sessions
- SSE streaming: tokens, live citation events, agent steps, final resolved geometry
- Checkpoint revert forks a branch; the old path stays readable and switchable
- Web app: login, document browser with live ingest progress, chat with citation
  pills + hover cards, PDF viewer with highlight overlay, media viewer with
  transcript seek, branch switcher, topic management

### Tests
72 unit tests passing — citation marker streaming at every chunk size,
adversarial split points, geometry mapping, line merging, truncated-JSON repair,
Korean sentence segmentation, verify-node fix application, table cell
selection and `calc`'s restricted expression evaluator.

---

## Implemented, not yet exercised against a live stack

Code-complete and unit tested where the logic is pure, but none of these have
cleared the bar above — a live model, a live Postgres, real data — the same
bar every "Verified working" entry already met.

- **Verify/reflect node** — wired into the graph (`app/agent/nodes/verify.py`),
  reusing compose's live citation parser so a fix that cites an
  already-offered source keeps its pill number. 5 unit tests cover
  hedge/cite/keep/unknown-source-id/non-matching-sentence. **Never run
  against a real LLM** — no live agent turn has triggered it yet.
- **Table/numeric tools** — `query_table`/`calc` (pure, unit tested) plus
  `complete_with_tools`, a new multi-round `tool_choice="auto"` loop in
  `llm_client.py`. **The loop itself is unverified against this stack's
  Hermes parser** — only the forced-single-function path (`complete_json`)
  has ever actually run against the served model; `tool_choice="auto"` with
  multiple tools is a genuinely different request shape.
- **Eval harness** (`make eval`) — `api/scripts/run_eval.py` plus a 10-case
  gold set derived and cross-checked against `scripts/make_samples.py`'s own
  generated contract text. **Never actually run** — needs a live stack with
  the sample documents ingested.
- **Admin UI** — `/admin` page, backend `AdminUserOut`. Build-verified: real
  `docker compose build` for `api`/`web` succeeded, both booted, `next
  build`/`tsc --noEmit` passed. **Not functionally verified** — no one has
  created, edited, or deactivated a user through the running UI yet.

---

## Bugs found and fixed during verification

Recorded because each was invisible until a specific test forced it out.

1. **Multi-turn citation collapse.** Stored answers contain `[[cite:N]]` tokens.
   Feeding them back as conversation history made the model imitate the syntax
   and emit `[[cite:S1]]`, which the parser does not recognise — so every
   follow-up answer silently lost all citations. Fixed by stripping tokens from
   history; the stripper deliberately matches malformed variants too, or a
   polluted transcript keeps re-teaching the bad syntax.
2. **Guided JSON produced empty results.** See README. Switched to forced tool
   calls.
3. **xgrammar whitespace stall.** Guided decoding could emit whitespace forever
   mid-object until the token limit. `disable_any_whitespace` plus a
   truncated-JSON repair path.
4. **Heading detection collapsed** on documents with many short sections: the
   median span font size landed *on* the heading size. Fixed by weighting font
   size by character count.
5. **Blank trailing pages triggered OCR.** A page with no text, no images and no
   drawings is blank, not scanned.
6. **Docling dropped body text on Korean scans** — 8 elements where the OCR
   engine alone found 13. Switched to calling OCR directly, which also yields
   line-level boxes (better citation geometry). Docling remains selectable via
   `OCR_LAYOUT_ENGINE=docling` for its table-structure recovery.
7. **OCR line fragmentation** broke reading order; fragments on one visual line
   are now merged before grouping.
8. **YAML folded scalars strip quotes**, mangling the JSON argument passed to
   vLLM. The vLLM services now run through a shell.
9. **nginx refused to start** when an upstream host was unresolvable, so a
   loading replica took the gateway down with it.
10. **`sparsevec` ORM assignment** needs a `SparseVector` object; the text form
    only works in an explicit SQL cast.
11. **Borderless tables** were invisible to the line-based table finder. Both
    strategies now run and merge.

---

## Not yet done

| Area | State |
|---|---|
| **Audio/video end to end** | Code paths written (ffmpeg → Whisper → timestamped chunks → seek-on-click) but **never run against a real recording**. Needs a Korean audio file to verify. |
| **HWP/HWPX** | H2Orestart is installed and registered in the worker image, and the conversion path is wired. **Not tested on a real .hwp file** — none was available. Test with your own documents early; complex Korean layouts may shift. |
| **Whole-corpus escalation** | Implemented in `researcher_node` and the `out_of_scope` flag is plumbed through to the UI, but not yet exercised by a test that forces it. |
| **Web search** | Deliberately deferred. Tool interface and provider adapter are stubbed behind `WEB_SEARCH_ENABLED=false`. |
| **LangGraph checkpointer** | The turn driver is hand-rolled asyncio for streaming clarity. `langgraph` is a dependency but `AsyncPostgresSaver` is not wired; user-facing branching does not depend on it. |
| **Concurrency** | Single-user tested. The per-session run lock and queue are implemented but not load-tested. |

---

## Known quality limits

- **Korean OCR accuracy is ~92–95%** on scans. Measured on the same page:
  EasyOCR 91.8% (9.7s), Tesseract 95.2% (0.6s, but scatters spurious spaces
  between syllables). EasyOCR is the default because citation snippets are shown
  to users and it reads more naturally. Errors propagate into summaries and
  retrieval — the *geometry* stays exact, so highlights remain correct even when
  the transcribed text is not. Evaluate `OCR_ENGINE` against your real scans.
- **Borderless tables** are extracted as text rows, not cell structure.
- **Topic auto-merge is conservative** (0.85). On observed data, "계약" vs
  "계약/법무" scored 0.75 while genuinely distinct pairs scored ≤0.57. Anything in
  0.70–0.85 is surfaced for a human rather than merged, because a single global
  threshold gets fragile as the taxonomy grows.

---

## Suggested order on a new machine

1. `make gpu` and `make dev`, then confirm `make test` passes and `/ready` reports all
   three dependencies healthy.
2. `make samples && make ingest FILE=samples/...`, then `make citation-check` and
   **look at the PNGs**. If highlights are off, nothing downstream matters.
3. Ingest a handful of your own real documents — especially **.hwp files and a
   recording**, the two untested paths.
4. `make eval` — the gold set and harness already exist; this is the first
   time it will actually run against a live model.
5. Ask a question that plausibly needs a hedge/citation fix, and a question
   over a table (e.g. the payment-schedule table in the sample supply
   contract), and watch the `verify`/`tables` step events and the `revision`
   SSE event fire. Neither has run against a real LLM yet — `tool_choice=
   "auto"` with multiple tools in particular is a new request shape for this
   stack's Hermes parser.
