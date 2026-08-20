"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Citation, Rect } from "@/lib/types";

/**
 * PDF viewer with citation highlighting.
 *
 * The geometry contract with the API: rects are in PDF points with a *top-left*
 * origin, alongside the page width the API recorded. pdf.js gives us a viewport
 * in CSS pixels for a chosen scale, so the overlay simply multiplies by
 * viewport.width / pageWidthInPoints. Everything is positioned against the same
 * canvas box, which is what keeps a highlight glued to its text at any zoom.
 */
type PdfDoc = {
  numPages: number;
  getPage: (n: number) => Promise<any>;
  destroy: () => void;
};

export default function PdfViewer({
  documentId,
  citation,
  className,
}: {
  documentId: string;
  citation: Citation | null;
  className?: string;
}) {
  const [doc, setDoc] = useState<PdfDoc | null>(null);
  const [numPages, setNumPages] = useState(0);
  const [page, setPage] = useState(1);
  const [scale, setScale] = useState(1.25);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const renderTask = useRef<any>(null);
  // Page size in PDF points, needed to scale the stored rects.
  const pageSize = useRef<{ width: number; height: number }>({ width: 0, height: 0 });

  // ---- load the document ------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        // The worker is copied into /public at build time (see Dockerfile), so
        // it is served from our own origin rather than a CDN the CSP would block.
        pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";

        const task = pdfjs.getDocument({
          url: `/api/proxy/documents/${documentId}/pdf`,
          withCredentials: true,
        });
        const loaded = await task.promise;
        if (cancelled) {
          loaded.destroy();
          return;
        }
        setDoc(loaded as unknown as PdfDoc);
        setNumPages(loaded.numPages);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "PDF를 불러오지 못했습니다");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [documentId]);

  useEffect(() => () => doc?.destroy(), [doc]);

  // ---- jump to the cited page -------------------------------------------
  const target = citation?.rects?.[0]?.page_no ?? citation?.page_no ?? null;
  useEffect(() => {
    if (target) setPage(target);
  }, [target, citation?.idx]);

  // ---- render the current page ------------------------------------------
  const renderPage = useCallback(async () => {
    if (!doc || !canvasRef.current) return;
    const canvas = canvasRef.current;

    // Cancel any in-flight render: switching pages quickly otherwise paints the
    // previous page over the new one.
    if (renderTask.current) {
      try {
        renderTask.current.cancel();
      } catch {
        /* already finished */
      }
    }

    const p = await doc.getPage(page);
    const base = p.getViewport({ scale: 1 });
    pageSize.current = { width: base.width, height: base.height };

    const dpr = window.devicePixelRatio || 1;
    const viewport = p.getViewport({ scale });
    canvas.width = Math.floor(viewport.width * dpr);
    canvas.height = Math.floor(viewport.height * dpr);
    canvas.style.width = `${viewport.width}px`;
    canvas.style.height = `${viewport.height}px`;

    if (overlayRef.current) {
      overlayRef.current.style.width = `${viewport.width}px`;
      overlayRef.current.style.height = `${viewport.height}px`;
    }

    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    renderTask.current = p.render({ canvasContext: ctx, viewport, canvas });
    try {
      await renderTask.current.promise;
    } catch {
      /* cancelled by a newer render */
    }
  }, [doc, page, scale]);

  useEffect(() => {
    void renderPage();
  }, [renderPage]);

  // ---- highlight rectangles for this page --------------------------------
  const highlights = useMemo(() => {
    if (!citation) return [];
    const rects: Rect[] = citation.rects?.length
      ? citation.rects
      : citation.page_no
        ? []
        : [];
    return rects.filter((r) => r.page_no === page);
  }, [citation, page]);

  const factor = pageSize.current.width > 0 ? scale : 1;

  // Scroll the first highlight into view once it exists.
  useEffect(() => {
    if (!highlights.length || !scrollRef.current) return;
    const id = requestAnimationFrame(() => {
      const el = scrollRef.current?.querySelector<HTMLElement>("[data-highlight='0']");
      el?.scrollIntoView({ block: "center", behavior: "smooth" });
    });
    return () => cancelAnimationFrame(id);
  }, [highlights, page, scale]);

  if (error) {
    return (
      <div className={className}>
        <div className="m-4 rounded border border-red-200 bg-red-50 p-3 text-[13px] text-red-700">
          {error}
        </div>
      </div>
    );
  }

  return (
    <div className={`flex h-full flex-col ${className ?? ""}`}>
      <div className="flex shrink-0 items-center gap-2 border-b border-line px-3 py-1.5 text-[12px]">
        <button
          className="rounded px-1.5 py-0.5 text-ink-muted hover:bg-gray-100 disabled:opacity-40"
          onClick={() => setPage((p) => Math.max(1, p - 1))}
          disabled={page <= 1}
        >
          ‹
        </button>
        <span className="tabular-nums text-ink-soft">
          {page} / {numPages || "–"}
        </span>
        <button
          className="rounded px-1.5 py-0.5 text-ink-muted hover:bg-gray-100 disabled:opacity-40"
          onClick={() => setPage((p) => Math.min(numPages, p + 1))}
          disabled={page >= numPages}
        >
          ›
        </button>
        <span className="mx-1 h-3 w-px bg-line" />
        <button
          className="rounded px-1.5 py-0.5 text-ink-muted hover:bg-gray-100"
          onClick={() => setScale((s) => Math.max(0.5, +(s - 0.25).toFixed(2)))}
        >
          −
        </button>
        <span className="tabular-nums text-ink-muted">{Math.round(scale * 100)}%</span>
        <button
          className="rounded px-1.5 py-0.5 text-ink-muted hover:bg-gray-100"
          onClick={() => setScale((s) => Math.min(3, +(s + 0.25).toFixed(2)))}
        >
          +
        </button>
        <span className="ml-auto">
          <a
            href={`/api/proxy/documents/${documentId}/original`}
            className="text-accent hover:underline"
          >
            원본 내려받기
          </a>
        </span>
      </div>

      <div ref={scrollRef} className="scrollbar-thin flex-1 overflow-auto bg-gray-100 p-4">
        {loading && <div className="p-6 text-[13px] text-ink-muted">문서를 불러오는 중…</div>}
        <div className="relative mx-auto w-fit shadow-sm">
          <canvas ref={canvasRef} className="block bg-white" />
          <div ref={overlayRef} className="pointer-events-none absolute left-0 top-0">
            {highlights.map((r, i) => {
              const [x0, y0, x1, y1] = r.bbox;
              return (
                <div
                  key={i}
                  data-highlight={i}
                  className="absolute rounded-[2px] bg-amber-300/40 ring-1 ring-amber-500/70"
                  style={{
                    left: x0 * factor,
                    top: y0 * factor,
                    width: Math.max(2, (x1 - x0) * factor),
                    height: Math.max(2, (y1 - y0) * factor),
                  }}
                />
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
