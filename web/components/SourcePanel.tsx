"use client";

import PdfViewer from "./PdfViewer";
import MediaViewer from "./MediaViewer";
import { citationLocator } from "./CitationPill";
import type { Citation, Doc } from "@/lib/types";

/** The right-hand panel a citation pill opens. */
export default function SourcePanel({
  doc,
  citation,
  onClose,
}: {
  doc: Doc;
  citation: Citation;
  onClose: () => void;
}) {
  const isMedia = doc.source_kind === "audio" || doc.source_kind === "video";

  return (
    <aside className="flex w-[46%] min-w-[420px] max-w-[760px] shrink-0 flex-col border-l border-line bg-white">
      <header className="flex shrink-0 items-start gap-2 border-b border-line px-3 py-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-1.5">
            <span className="flex h-[18px] min-w-[18px] items-center justify-center rounded-[5px] bg-accent-soft px-[5px] text-[11px] font-semibold text-accent">
              {citation.idx}
            </span>
            <span className="truncate text-[13px] font-semibold">{doc.filename}</span>
            <span className="shrink-0 text-[12px] text-ink-muted">
              {citationLocator(citation)}
            </span>
          </div>
          {citation.heading_path && (
            <p className="mt-0.5 truncate text-[11.5px] text-ink-muted">
              {citation.heading_path}
            </p>
          )}
        </div>
        <button
          onClick={onClose}
          className="shrink-0 rounded px-2 py-1 text-[13px] text-ink-muted hover:bg-gray-100"
          aria-label="닫기"
        >
          ✕
        </button>
      </header>

      {citation.out_of_scope && (
        <div className="shrink-0 border-b border-amber-200 bg-amber-50 px-3 py-1.5 text-[11.5px] text-amber-800">
          이 세션에서 선택하지 않은 문서입니다. 에이전트가 전체 문서에서 찾았습니다.
        </div>
      )}

      <div className="min-h-0 flex-1">
        {isMedia ? (
          <MediaViewer
            documentId={doc.id}
            kind={doc.source_kind as "audio" | "video"}
            citation={citation}
          />
        ) : doc.has_pdf ? (
          <PdfViewer documentId={doc.id} citation={citation} />
        ) : (
          <div className="p-4">
            <p className="mb-2 text-[12px] text-ink-muted">
              이 문서는 미리보기를 만들지 못했습니다. 인용된 본문만 표시합니다.
            </p>
            <blockquote className="border-l-2 border-cite bg-cite-soft/40 p-3 text-[13px] leading-relaxed">
              {citation.snippet}
            </blockquote>
          </div>
        )}
      </div>
    </aside>
  );
}
