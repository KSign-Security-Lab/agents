"""Layout extraction: text plus the geometry that makes citations precise.

Two routes, chosen per page:

* **PyMuPDF** for pages that carry a text layer. It yields exact line-level
  bounding boxes with no model inference, and every Office/HWP file we convert
  has a text layer — so this fast, deterministic path covers the large majority
  of documents. PyMuPDF's own table finder gives per-cell boxes.
* **Docling** for pages with no text layer, where OCR and layout inference are
  genuinely needed.

Whichever route runs, the output is the same: a reading-ordered element list and
a *line-granular span map* from character offsets in the page text to page
rectangles. That span map is what later lets a citation highlight the exact
sentences behind a claim rather than a whole paragraph.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from api.app.config import settings
from api.app.db.models import ElementKind

log = logging.getLogger("ingest.extract")

# A page with fewer than this many extractable characters is treated as scanned.
MIN_TEXT_CHARS_PER_PAGE = 16
# Heading detection: a line whose font is this much larger than the body median.
HEADING_SIZE_RATIO = 1.15


@dataclass(slots=True)
class Span:
    """A run of text and where it came from.

    For paged documents that is a page rectangle; for transcripts it is a time
    range. One of the two is always set, and it is what a citation resolves to.
    """

    text_start: int                    # offset into the element's text
    text_end: int
    page_no: int | None = None
    bbox: list[float] | None = None    # [x0,y0,x1,y1] PDF points, top-left origin
    t_start_ms: int | None = None
    t_end_ms: int | None = None


@dataclass(slots=True)
class Element:
    kind: ElementKind
    page_no: int
    reading_order: int
    text: str
    bbox: list[float] | None = None
    level: int | None = None
    table_json: dict | None = None
    spans: list[Span] = field(default_factory=list)


@dataclass(slots=True)
class Page:
    page_no: int              # 1-based
    width: float              # PDF points, as displayed (rotation applied)
    height: float
    rotation: int
    has_text_layer: bool


@dataclass(slots=True)
class Extracted:
    pages: list[Page]
    elements: list[Element]
    ocr_pages: list[int] = field(default_factory=list)

    @property
    def scanned(self) -> bool:
        return bool(self.pages) and all(not p.has_text_layer for p in self.pages)


def extract(pdf_path: str | Path) -> Extracted:
    import pymupdf

    pdf_path = Path(pdf_path)
    pages: list[Page] = []
    elements: list[Element] = []
    ocr_pages: list[int] = []
    order = 0

    with pymupdf.open(str(pdf_path)) as doc:
        for i, page in enumerate(doc):
            page_no = i + 1
            rect = page.rect          # already reflects /Rotate
            raw_text = page.get_text("text") or ""
            has_text = len(raw_text.strip()) >= MIN_TEXT_CHARS_PER_PAGE

            pages.append(Page(page_no=page_no, width=float(rect.width),
                              height=float(rect.height), rotation=int(page.rotation),
                              has_text_layer=has_text))

            if has_text:
                page_elements, order = _extract_page_pymupdf(page, page_no, order)
                elements.extend(page_elements)
            elif _has_visual_content(page):
                ocr_pages.append(page_no)
            else:
                # Genuinely blank (word processors routinely leave a trailing
                # empty page). OCRing it would burn seconds per document to
                # produce nothing.
                log.debug("page %d is blank; skipping OCR", page_no)

    if ocr_pages:
        log.info("%s: %d page(s) have no text layer, routing to OCR",
                 pdf_path.name, len(ocr_pages))
        try:
            ocr_elements = _extract_pages_ocr(pdf_path, ocr_pages, start_order=order)
            elements.extend(ocr_elements)
            for p in pages:
                if p.page_no in ocr_pages:
                    p.has_text_layer = False
        except Exception as exc:  # noqa: BLE001
            log.error("OCR failed for %s: %s", pdf_path.name, exc)

    elements.sort(key=lambda e: (e.page_no, e.reading_order))
    return Extracted(pages=pages, elements=elements, ocr_pages=ocr_pages)


# ------------------------------------------------------------------ pymupdf --
def _extract_page_pymupdf(page, page_no: int, order: int) -> tuple[list[Element], int]:
    import pymupdf

    elements: list[Element] = []

    # Tables first, so their text is not also emitted as loose paragraphs.
    table_rects: list[pymupdf.Rect] = []
    for t in _find_tables(page, page_no):
        tbl = _table_element(t, page_no, order)
        if tbl:
            elements.append(tbl)
            order += 1
            table_rects.append(pymupdf.Rect(t.bbox))

    data = page.get_text("dict")
    body_size = _body_font_size(data)

    for block in data.get("blocks", []):
        if block.get("type") != 0:          # 1 == image
            continue
        bbox = pymupdf.Rect(block["bbox"])
        if any(_mostly_inside(bbox, tr) for tr in table_rects):
            continue

        lines: list[tuple[str, list[float], float]] = []
        for ln in block.get("lines", []):
            text = "".join(s.get("text", "") for s in ln.get("spans", []))
            if not text.strip():
                continue
            span_sizes = [s["size"] for s in ln.get("spans", []) if (s.get("text") or "").strip()]
            lines.append((text, [float(v) for v in ln["bbox"]],
                          max(span_sizes) if span_sizes else body_size))
        if not lines:
            continue

        # Assemble the block text and record where each line landed.
        parts: list[str] = []
        spans: list[Span] = []
        cursor = 0
        for text, lbbox, _ in lines:
            parts.append(text)
            spans.append(Span(text_start=cursor, text_end=cursor + len(text),
                              page_no=page_no, bbox=lbbox))
            cursor += len(text) + 1        # for the joining newline
        block_text = "\n".join(parts)

        max_size = max(s for _, _, s in lines)
        is_heading = (
            body_size > 0
            and max_size >= body_size * HEADING_SIZE_RATIO
            and len(block_text) <= 120
            and block_text.count("\n") <= 1
        )

        elements.append(Element(
            kind=ElementKind.heading if is_heading else ElementKind.paragraph,
            page_no=page_no,
            reading_order=order,
            text=block_text,
            bbox=[float(v) for v in block["bbox"]],
            level=_heading_level(max_size, body_size) if is_heading else None,
            spans=spans,
        ))
        order += 1

    return elements, order


def _find_tables(page, page_no: int) -> list:
    """Find tables, falling back to the text-alignment strategy for borderless ones.

    Korean office documents frequently use tables with no ruling lines, and
    LibreOffice also drops CSS borders when converting HTML. The default
    line-based strategy sees nothing in those cases, so the whole table would be
    emitted as loose paragraphs and a cited figure could not name its cell.

    The text strategy is only consulted when the line strategy finds nothing, and
    its results are screened by ``_plausible_table`` because column-aligned prose
    can otherwise be mistaken for a grid.
    """
    import pymupdf

    try:
        found = list(page.find_tables().tables)
    except Exception as exc:  # noqa: BLE001 - detection is best-effort
        log.debug("line-based table detection failed on page %d: %s", page_no, exc)
        found = []

    # The text strategy runs even when the line strategy succeeded: a page can
    # mix a ruled table with a borderless one, and returning only the ruled one
    # would leave the other flattened into loose paragraphs.
    try:
        candidates = list(page.find_tables(strategy="text").tables)
    except Exception as exc:  # noqa: BLE001
        log.debug("text-based table detection failed on page %d: %s", page_no, exc)
        candidates = []

    existing = [pymupdf.Rect(t.bbox) for t in found]
    added = 0
    for cand in candidates:
        rect = pymupdf.Rect(cand.bbox)
        if any(_mostly_inside(rect, e) or _mostly_inside(e, rect) for e in existing):
            continue
        if not _plausible_table(cand):
            continue
        found.append(cand)
        existing.append(rect)
        added += 1
    if added:
        log.debug("page %d: %d borderless table(s) added alongside %d ruled",
                  page_no, added, len(found) - added)
    return found


def _plausible_table(table, *, min_rows: int = 2, min_cols: int = 2,
                     min_fill: float = 0.6) -> bool:
    """Screen a borderless candidate: enough rows and columns, a consistent
    column count, and most cells actually populated."""
    try:
        grid = table.extract()
    except Exception:  # noqa: BLE001
        return False
    if not grid or len(grid) < min_rows:
        return False

    widths = {len(r) for r in grid}
    n_cols = max(widths)
    if n_cols < min_cols or len(widths) > 2:
        return False

    total = sum(len(r) for r in grid)
    filled = sum(1 for r in grid for c in r if (c or "").strip())
    return total > 0 and filled / total >= min_fill


def _table_element(table, page_no: int, order: int) -> Element | None:
    """A table becomes one element carrying both a markdown rendering (for the
    model to read) and per-cell boxes (so a cited number can highlight its cell)."""
    try:
        grid = table.extract()
    except Exception:  # noqa: BLE001
        return None
    if not grid:
        return None

    cells: list[dict] = []
    for r, row in enumerate(grid):
        for c, val in enumerate(row):
            cells.append({"r": r, "c": c, "text": (val or "").strip()})

    # Per-cell rectangles, so a cited figure can highlight the cell it came from.
    # PyMuPDF exposes these as table.rows[r].cells[c] (plain 4-tuples).
    by_rc = {(c["r"], c["c"]): c for c in cells}
    try:
        for r, row in enumerate(getattr(table, "rows", []) or []):
            for c, rect in enumerate(getattr(row, "cells", []) or []):
                if rect is None:
                    continue
                cell = by_rc.get((r, c))
                if cell is not None:
                    cell["bbox"] = [float(v) for v in rect]
    except Exception as exc:  # noqa: BLE001 - cell geometry is a bonus, not required
        log.debug("table cell geometry unavailable: %s", exc)

    md = _to_markdown(grid)
    return Element(
        kind=ElementKind.table,
        page_no=page_no,
        reading_order=order,
        text=md,
        bbox=[float(v) for v in table.bbox],
        table_json={"n_rows": len(grid), "n_cols": max(len(r) for r in grid), "cells": cells},
        # A table is cited as a whole; sentence-level narrowing is meaningless here.
        spans=[Span(text_start=0, text_end=len(md), page_no=page_no,
                    bbox=[float(v) for v in table.bbox])],
    )


# --------------------------------------------------------------------- ocr ---
def _extract_pages_ocr(pdf_path: Path, page_numbers: list[int], *,
                       start_order: int) -> list[Element]:
    """Read pages that carry no text layer.

    Direct OCR is the default. Docling was tried first and measured worse on a
    Korean scan: its layout/assembly step returned 8 elements where the OCR
    engine alone found 13 text regions, silently dropping most body paragraphs
    while keeping the headings. Calling the OCR engine ourselves also yields
    line-level boxes, which is *better* geometry for citations than Docling's
    paragraph-level provenance.

    Docling remains selectable (OCR_LAYOUT_ENGINE=docling) because it does
    recover table cell structure on scans, which the direct path does not.
    """
    if settings.ocr_layout_engine == "docling":
        return _ocr_via_docling(pdf_path, page_numbers, start_order=start_order)
    return _ocr_direct(pdf_path, page_numbers, start_order=start_order)


def _ocr_direct(pdf_path: Path, page_numbers: list[int], *,
                start_order: int) -> list[Element]:
    import pymupdf

    reader = _ocr_reader()
    elements: list[Element] = []
    order = start_order
    dpi = settings.ocr_dpi
    # OCR works in image pixels; everything we store is in PDF points.
    to_points = 72.0 / dpi

    with pymupdf.open(str(pdf_path)) as doc:
        for page_no in page_numbers:
            page = doc[page_no - 1]
            pix = page.get_pixmap(dpi=dpi)
            lines = reader(pix.tobytes("png"))
            if not lines:
                log.warning("%s page %d: OCR found no text", pdf_path.name, page_no)
                continue

            scaled = [
                (text, [v * to_points for v in bbox], conf)
                for text, bbox, conf in lines
                if text.strip() and conf >= settings.ocr_min_confidence
            ]
            if not scaled:
                continue

            # OCR routinely reports one visual line as several fragments. Merge
            # them before anything else: otherwise a clause split at a comma
            # becomes two out-of-order "paragraphs".
            visual = _merge_visual_lines(scaled)
            body_h = _median([b[3] - b[1] for _, b, _ in visual])

            for block in _group_lines(visual, body_h):
                texts = [t for t, _, _ in block]
                boxes = [b for _, b, _ in block]
                text = "\n".join(texts)

                spans: list[Span] = []
                cursor = 0
                for t, b in zip(texts, boxes):
                    spans.append(Span(text_start=cursor, text_end=cursor + len(t),
                                      page_no=page_no, bbox=[round(v, 2) for v in b]))
                    cursor += len(t) + 1

                max_h = max(b[3] - b[1] for b in boxes)
                is_heading = _looks_like_heading(text, max_h, body_h, len(block))
                elements.append(Element(
                    kind=ElementKind.heading if is_heading else ElementKind.paragraph,
                    page_no=page_no,
                    reading_order=order,
                    text=text,
                    bbox=[round(v, 2) for v in _union(boxes)],
                    level=_heading_level(max_h, body_h) if is_heading else None,
                    spans=spans,
                ))
                order += 1

    return elements


def _merge_visual_lines(fragments: list[tuple[str, list[float], float]]
                        ) -> list[tuple[str, list[float], float]]:
    """Join OCR fragments that sit on the same visual line.

    Engines split a line wherever they like — often at punctuation — so
    "하자보증 기간은 검수일로부터" and "2년으로 한다." arrive as separate regions. Left
    unmerged they sort into different blocks and the sentence is torn apart.
    """
    if not fragments:
        return []

    rows: list[list[tuple[str, list[float], float]]] = []
    for frag in sorted(fragments, key=lambda f: (f[1][1], f[1][0])):
        placed = False
        fh = frag[1][3] - frag[1][1]
        for row in rows:
            rb = _union([b for _, b, _ in row])
            overlap = min(rb[3], frag[1][3]) - max(rb[1], frag[1][1])
            ref = min(rb[3] - rb[1], fh)
            if ref > 0 and overlap / ref > 0.5:
                row.append(frag)
                placed = True
                break
        if not placed:
            rows.append([frag])

    merged: list[tuple[str, list[float], float]] = []
    for row in rows:
        row.sort(key=lambda f: f[1][0])
        text = " ".join(t.strip() for t, _, _ in row if t.strip())
        boxes = [b for _, b, _ in row]
        confs = [c for _, _, c in row]
        merged.append((text, _union(boxes), sum(confs) / len(confs)))
    merged.sort(key=lambda f: (f[1][1], f[1][0]))
    return merged


def _looks_like_heading(text: str, height: float, body_h: float, n_lines: int) -> bool:
    """Heading test for OCR output.

    Height ratio alone is unreliable on a scan, so a Korean clause marker
    ("제3조 (…)") counts as well — those are the section boundaries the chunker
    needs, and missing them would flatten the whole document into one section.
    """
    import re

    if n_lines != 1 or len(text) > 120:
        return False
    if body_h > 0 and height >= body_h * HEADING_SIZE_RATIO:
        return True
    return bool(re.match(r"^\s*제\s*\d+\s*조\s*[\(（]", text))


def _group_lines(lines: list[tuple[str, list[float], float]],
                 body_h: float) -> list[list[tuple[str, list[float], float]]]:
    """Group visual lines into blocks by vertical gap, breaking at headings.

    A heading always starts its own block: the chunker relies on headings being
    separate elements to build the section path a citation is labelled with.
    """
    if not lines:
        return []
    gap_limit = max(body_h * 0.9, 4.0)
    blocks: list[list[tuple[str, list[float], float]]] = []

    for item in lines:
        text, box, _ = item
        height = box[3] - box[1]
        starts_block = _looks_like_heading(text, height, body_h, 1)

        if not blocks or starts_block:
            blocks.append([item])
            continue

        prev = blocks[-1][-1]
        prev_is_heading = _looks_like_heading(prev[0], prev[1][3] - prev[1][1], body_h, 1)
        vertical_gap = box[1] - prev[1][3]
        indent_shift = abs(box[0] - prev[1][0])

        if prev_is_heading or vertical_gap > gap_limit or indent_shift > body_h * 6:
            blocks.append([item])
        else:
            blocks[-1].append(item)

    return blocks


def _ocr_reader():
    """Return ``png_bytes -> [(text, [x0,y0,x1,y1], confidence)]`` for the
    configured engine. Boxes are in image pixels."""
    engine = settings.ocr_engine
    langs = settings.ocr_lang_list

    if engine == "easyocr":
        import easyocr
        import numpy as np
        from PIL import Image
        import io

        reader = easyocr.Reader(langs, gpu=settings.ocr_use_gpu, verbose=False)

        def run(png: bytes):
            img = np.array(Image.open(io.BytesIO(png)).convert("RGB"))
            out = []
            for quad, text, conf in reader.readtext(img, detail=1, paragraph=False):
                xs = [float(p[0]) for p in quad]
                ys = [float(p[1]) for p in quad]
                out.append((text, [min(xs), min(ys), max(xs), max(ys)], float(conf)))
            return out

        return run

    if engine == "rapidocr":
        from rapidocr_onnxruntime import RapidOCR
        import numpy as np
        from PIL import Image
        import io

        engine_obj = RapidOCR()

        def run(png: bytes):
            img = np.array(Image.open(io.BytesIO(png)).convert("RGB"))
            result, _ = engine_obj(img)
            out = []
            for quad, text, conf in (result or []):
                xs = [float(p[0]) for p in quad]
                ys = [float(p[1]) for p in quad]
                out.append((text, [min(xs), min(ys), max(xs), max(ys)], float(conf)))
            return out

        return run

    # tesseract
    import io
    import subprocess

    iso3 = {"ko": "kor", "en": "eng", "ja": "jpn", "zh": "chi_sim"}
    lang = "+".join(iso3.get(l, l) for l in langs)

    def run(png: bytes):
        proc = subprocess.run(
            ["tesseract", "stdin", "stdout", "-l", lang, "--psm", "6", "tsv"],
            input=png, capture_output=True, timeout=300,
        )
        out: list[tuple[str, list[float], float]] = []
        rows = proc.stdout.decode("utf-8", "replace").splitlines()
        # Tesseract TSV is word-level; merge words that share a line number.
        current: dict[tuple, list] = {}
        for row in rows[1:]:
            f = row.split("\t")
            if len(f) < 12 or f[11].strip() in ("", "-1"):
                continue
            key = tuple(f[1:5])          # page/block/par/line
            left, top, w, h = (float(f[6]), float(f[7]), float(f[8]), float(f[9]))
            conf = float(f[10]) / 100.0
            entry = current.setdefault(key, [[], [left, top, left + w, top + h], []])
            entry[0].append(f[11])
            box = entry[1]
            entry[1] = [min(box[0], left), min(box[1], top),
                        max(box[2], left + w), max(box[3], top + h)]
            entry[2].append(conf)
        for texts, box, confs in current.values():
            out.append((" ".join(texts), box, sum(confs) / len(confs) if confs else 0.0))
        return out

    return run


def _ocr_via_docling(pdf_path: Path, page_numbers: list[int], *,
                     start_order: int) -> list[Element]:
    """Docling route: worse text recall on Korean scans in our measurement, but it
    does reconstruct table cell structure. Opt in with OCR_LAYOUT_ENGINE=docling."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = True
    opts.do_table_structure = True
    opts.generate_page_images = False
    _configure_docling_ocr(opts)

    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    result = converter.convert(str(pdf_path), page_range=(min(page_numbers), max(page_numbers)))

    elements: list[Element] = []
    order = start_order
    wanted = set(page_numbers)

    for item, _level in result.document.iterate_items():
        text = (getattr(item, "text", "") or "").strip()
        prov = getattr(item, "prov", None) or []
        if not text or not prov:
            continue
        p = prov[0]
        page_no = int(getattr(p, "page_no", 0) or 0)
        if page_no not in wanted:
            continue
        bbox = _docling_bbox(p, result, page_no)
        elements.append(Element(
            kind=_docling_kind(item), page_no=page_no, reading_order=order,
            text=text, bbox=bbox,
            spans=[Span(text_start=0, text_end=len(text), page_no=page_no, bbox=bbox)]
            if bbox else [],
        ))
        order += 1
    return elements


def _configure_docling_ocr(opts) -> None:
    langs = settings.ocr_lang_list
    engine = settings.ocr_engine
    try:
        if engine == "easyocr":
            from docling.datamodel.pipeline_options import EasyOcrOptions

            opts.ocr_options = EasyOcrOptions(lang=langs)
        elif engine == "rapidocr":
            from docling.datamodel.pipeline_options import RapidOcrOptions

            opts.ocr_options = RapidOcrOptions()
        else:
            from docling.datamodel.pipeline_options import TesseractCliOcrOptions

            iso3 = {"ko": "kor", "en": "eng", "ja": "jpn", "zh": "chi_sim"}
            opts.ocr_options = TesseractCliOcrOptions(
                lang=[iso3.get(l, l) for l in langs])
    except Exception as exc:  # noqa: BLE001
        log.warning("could not configure OCR engine %r (%s); using Docling default",
                    engine, exc)


def _docling_bbox(prov, result, page_no: int) -> list[float] | None:
    """Convert Docling provenance to top-left-origin PDF points.

    Docling reports boxes in a bottom-left origin space; PyMuPDF and PDF.js both
    use top-left. Getting this wrong is exactly the "highlight sits in the wrong
    place vertically" bug, so the flip is explicit.
    """
    bbox = getattr(prov, "bbox", None)
    if bbox is None:
        return None
    try:
        page = result.document.pages.get(page_no)
        page_h = float(page.size.height) if page and page.size else None
    except Exception:  # noqa: BLE001
        page_h = None

    l, t, r, b = float(bbox.l), float(bbox.t), float(bbox.r), float(bbox.b)
    origin = str(getattr(bbox, "coord_origin", "")).upper()
    if "BOTTOMLEFT" in origin and page_h:
        return [l, page_h - max(t, b), r, page_h - min(t, b)]
    return [l, min(t, b), r, max(t, b)]


def _docling_kind(item) -> ElementKind:
    label = str(getattr(item, "label", "")).lower()
    if "title" in label or "header" in label or "section" in label:
        return ElementKind.heading
    if "table" in label:
        return ElementKind.table
    if "caption" in label:
        return ElementKind.caption
    if "list" in label:
        return ElementKind.list_item
    return ElementKind.paragraph


# ------------------------------------------------------------------ helpers --
def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _union(boxes: list[list[float]]) -> list[float]:
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _has_visual_content(page) -> bool:
    """Whether a text-free page holds anything OCR could read."""
    try:
        if page.get_images():
            return True
        return len(page.get_drawings()) > 0
    except Exception:  # noqa: BLE001
        return True     # if unsure, prefer OCR over silently dropping content


def _body_font_size(page_dict: dict) -> float:
    """The dominant font size, weighted by how many characters use it.

    Not the median of span sizes: headings are short but numerous (one span
    each), so on a document of many short sections the median lands *on* the
    heading size and every heading then fails the "larger than body" test.
    Weighting by character count makes body text dominate, which is what "body
    size" is supposed to mean.
    """
    weight: dict[float, int] = {}
    for b in page_dict.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            for sp in ln.get("spans", []):
                text = (sp.get("text") or "").strip()
                if not text:
                    continue
                size = round(float(sp["size"]), 1)
                weight[size] = weight.get(size, 0) + len(text)
    if not weight:
        return 0.0
    return max(weight.items(), key=lambda kv: kv[1])[0]


def _heading_level(size: float, body: float) -> int:
    if body <= 0:
        return 2
    ratio = size / body
    if ratio >= 1.6:
        return 1
    if ratio >= 1.35:
        return 2
    return 3


def _mostly_inside(inner, outer, threshold: float = 0.6) -> bool:
    overlap = inner & outer
    if overlap.is_empty or inner.get_area() <= 0:
        return False
    return overlap.get_area() / inner.get_area() >= threshold


def _to_markdown(grid: list[list[str | None]]) -> str:
    if not grid:
        return ""
    width = max(len(r) for r in grid)
    rows = [[(c or "").strip().replace("\n", " ") for c in r] + [""] * (width - len(r))
            for r in grid]
    out = ["| " + " | ".join(rows[0]) + " |",
           "| " + " | ".join(["---"] * width) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)
