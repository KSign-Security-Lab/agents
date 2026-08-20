"""Render a document's stored geometry onto its pages so a human can see whether
the boxes cover the right words.

"The highlight is 20px off" is the failure mode that silently ruins citation
UX and that no assertion catches. This writes PNGs with every span box drawn, so
the geometry is checked by looking at it.
"""
from __future__ import annotations

import sys
from pathlib import Path


def draw(pdf_path: Path, out_dir: Path, elements, *, dpi: int = 110) -> list[Path]:
    """elements: iterable of (page_no, bbox, kind) in PDF-point, top-left space."""
    import pymupdf

    out_dir.mkdir(parents=True, exist_ok=True)
    colours = {
        "heading": (0.85, 0.1, 0.1),
        "paragraph": (0.1, 0.35, 0.9),
        "table": (0.05, 0.6, 0.2),
        "span": (0.95, 0.55, 0.0),
    }
    by_page: dict[int, list] = {}
    for page_no, bbox, kind in elements:
        by_page.setdefault(page_no, []).append((bbox, kind))

    written: list[Path] = []
    with pymupdf.open(str(pdf_path)) as doc:
        for page_no, boxes in sorted(by_page.items()):
            page = doc[page_no - 1]
            for bbox, kind in boxes:
                rect = pymupdf.Rect(*bbox)
                page.draw_rect(rect, color=colours.get(kind, (0.5, 0.5, 0.5)),
                               width=0.8, fill=None)
            pix = page.get_pixmap(dpi=dpi)
            dst = out_dir / f"{pdf_path.stem}_p{page_no}.png"
            pix.save(str(dst))
            written.append(dst)
    return written


def main() -> None:
    from api.app.ingest import extract as ex

    if len(sys.argv) < 2:
        print("usage: python -m api.scripts.citation_check <pdf> [out_dir]")
        raise SystemExit(2)
    pdf = Path(sys.argv[1])
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "/out")

    result = ex.extract(pdf)
    print(f"{pdf.name}: {len(result.pages)} pages, {len(result.elements)} elements, "
          f"ocr_pages={result.ocr_pages}")
    for p in result.pages:
        print(f"  p.{p.page_no} {p.width:.0f}x{p.height:.0f} rot={p.rotation} "
              f"text_layer={p.has_text_layer}")

    boxes = []
    for e in result.elements:
        if e.bbox:
            boxes.append((e.page_no, e.bbox, e.kind.value))
        for s in e.spans:
            boxes.append((s.page_no, s.bbox, "span"))

    written = draw(pdf, out, boxes)
    print(f"wrote {len(written)} image(s) to {out}:")
    for w in written:
        print("  ", w)


if __name__ == "__main__":
    main()
