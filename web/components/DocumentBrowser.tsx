"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import type { Doc, Topic } from "@/lib/types";

const STAGE_LABEL: Record<string, string> = {
  pending: "대기 중",
  converting: "변환 중",
  extracting: "본문 추출 중",
  transcribing: "음성 인식 중",
  chunking: "분할 중",
  embedding: "임베딩 중",
  categorizing: "분류 중",
  ready: "준비됨",
  failed: "실패",
};

const KIND_ICON: Record<string, string> = {
  pdf: "📕", scanned: "🖨", office: "📘", hwp: "📗",
  image: "🖼", text: "📄", audio: "🎧", video: "🎬",
};

export default function DocumentBrowser({
  initialDocuments,
  topics,
}: {
  initialDocuments: Doc[];
  topics: Topic[];
}) {
  const [docs, setDocs] = useState(initialDocuments);
  const [topicId, setTopicId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [uploading, setUploading] = useState<string[]>([]);
  const [detail, setDetail] = useState<Doc | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    const res = await fetch("/api/proxy/documents");
    if (res.ok) setDocs(await res.json());
  }, []);

  // Ingest progress is broadcast workspace-wide, so uploads by anyone appear
  // and advance through their stages without a manual reload.
  useEffect(() => {
    const es = new EventSource("/api/proxy/events");
    const onChange = () => void refresh();
    es.addEventListener("document.status", onChange);
    es.addEventListener("document.categorized", onChange);
    return () => es.close();
  }, [refresh]);

  const upload = useCallback(
    async (files: FileList | File[]) => {
      const list = Array.from(files);
      setUploading((prev) => [...prev, ...list.map((f) => f.name)]);
      for (const file of list) {
        const body = new FormData();
        body.append("file", file);
        await fetch("/api/proxy/documents", { method: "POST", body }).catch(() => {});
        setUploading((prev) => prev.filter((n) => n !== file.name));
      }
      void refresh();
    },
    [refresh],
  );

  const filtered = useMemo(() => {
    let out = docs;
    if (topicId) out = out.filter((d) => d.topics.some((t) => t.id === topicId));
    if (query.trim()) {
      const q = query.trim().toLowerCase();
      out = out.filter(
        (d) =>
          d.filename.toLowerCase().includes(q) ||
          (d.summary ?? "").toLowerCase().includes(q),
      );
    }
    return out;
  }, [docs, topicId, query]);

  return (
    <>
      <header className="shrink-0 border-b border-line px-5 py-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h1 className="text-[15px] font-semibold">문서</h1>
            <p className="text-[12px] text-ink-muted">
              {docs.length}개 · 업로드하면 자동으로 분류됩니다
            </p>
          </div>
          <div className="flex items-center gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="파일명·요약 검색"
              className="w-52 rounded-lg border border-line px-2.5 py-1.5 text-[13px] outline-none focus:border-accent"
            />
            <button
              onClick={() => inputRef.current?.click()}
              className="rounded-lg bg-accent px-3 py-1.5 text-[13px] font-medium text-white"
            >
              업로드
            </button>
            <input
              ref={inputRef}
              type="file"
              multiple
              hidden
              onChange={(e) => e.target.files && void upload(e.target.files)}
            />
          </div>
        </div>

        <div className="mt-2.5 flex flex-wrap gap-1.5">
          <Chip active={topicId === null} onClick={() => setTopicId(null)}>
            전체 {docs.length}
          </Chip>
          {topics.map((t) => (
            <Chip key={t.id} active={topicId === t.id} onClick={() => setTopicId(t.id)}>
              {t.name} {t.doc_count}
            </Chip>
          ))}
        </div>
      </header>

      <div
        className="scrollbar-thin flex-1 overflow-y-auto p-5"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          if (e.dataTransfer.files.length) void upload(e.dataTransfer.files);
        }}
      >
        {uploading.length > 0 && (
          <div className="mb-3 rounded-lg border border-accent bg-accent-soft px-3 py-2 text-[12.5px] text-accent">
            업로드 중: {uploading.join(", ")}
          </div>
        )}

        {filtered.length === 0 ? (
          <div className="rounded-xl border-2 border-dashed border-line py-16 text-center">
            <p className="text-[13.5px] text-ink-soft">
              파일을 이 영역에 끌어다 놓거나 업로드 버튼을 누르세요
            </p>
            <p className="mt-1 text-[12px] text-ink-muted">
              PDF · 스캔본 · docx/xlsx/pptx · HWP · 이미지 · 텍스트 · 음성/영상
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3">
            {filtered.map((d) => (
              <button
                key={d.id}
                onClick={() => setDetail(d)}
                className="rounded-xl border border-line p-3 text-left transition-colors hover:border-accent"
              >
                <div className="flex items-start gap-2">
                  <span className="text-[16px]">{KIND_ICON[d.source_kind] ?? "📄"}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13.5px] font-medium">
                      {d.filename}
                    </span>
                    <span className="mt-0.5 block text-[11.5px] text-ink-muted">
                      {d.status === "ready"
                        ? d.page_count
                          ? `${d.page_count}쪽`
                          : d.duration_ms
                            ? `${Math.round(d.duration_ms / 60000)}분`
                            : ""
                        : STAGE_LABEL[d.status] ?? d.status}
                    </span>
                  </span>
                  {d.status !== "ready" && d.status !== "failed" && (
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent animate-pulse-soft" />
                  )}
                  {d.status === "failed" && (
                    <span className="shrink-0 text-[11px] text-red-600">실패</span>
                  )}
                </div>

                {d.summary && (
                  <p className="mt-2 line-clamp-2 text-[12px] leading-relaxed text-ink-soft">
                    {d.summary}
                  </p>
                )}

                {d.topics.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {d.topics.map((t) => (
                      <span
                        key={t.id}
                        className="rounded bg-gray-100 px-1.5 py-0.5 text-[10.5px] text-ink-soft"
                      >
                        {t.name}
                      </span>
                    ))}
                  </div>
                )}
              </button>
            ))}
          </div>
        )}
      </div>

      {detail && <DocumentDetail doc={detail} onClose={() => setDetail(null)} />}
    </>
  );
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        "rounded-full px-2.5 py-1 text-[12px]",
        active ? "bg-ink text-white" : "bg-gray-100 text-ink-soft hover:bg-gray-200",
      )}
    >
      {children}
    </button>
  );
}

function DocumentDetail({ doc, onClose }: { doc: Doc; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/20" onClick={onClose}>
      <div
        className="flex h-full w-[420px] flex-col bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-start gap-2 border-b border-line px-4 py-3">
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-[14px] font-semibold">{doc.filename}</h2>
            <p className="text-[11.5px] text-ink-muted">
              {doc.uploader?.name ?? "알 수 없음"} ·{" "}
              {new Date(doc.created_at).toLocaleDateString("ko-KR")} ·{" "}
              {(doc.size_bytes / 1024 / 1024).toFixed(1)}MB
            </p>
          </div>
          <button onClick={onClose} className="rounded px-2 py-1 text-ink-muted hover:bg-gray-100">
            ✕
          </button>
        </header>

        <div className="scrollbar-thin flex-1 overflow-y-auto p-4 text-[13px]">
          {doc.status === "failed" && (
            <div className="mb-3 rounded border border-red-200 bg-red-50 p-2.5 text-[12.5px] text-red-700">
              처리 실패: {doc.error}
            </div>
          )}
          {doc.source_kind === "scanned" && (
            <div className="mb-3 rounded border border-amber-200 bg-amber-50 p-2.5 text-[12px] text-amber-800">
              스캔 문서입니다. 본문은 OCR로 읽었으며, 인용 강조가 다소 넓게 표시될 수 있습니다.
            </div>
          )}

          {doc.summary && (
            <section className="mb-4">
              <h3 className="mb-1 text-[12px] font-semibold text-ink-soft">요약</h3>
              <p className="leading-relaxed text-ink-soft">{doc.summary}</p>
            </section>
          )}

          {doc.key_entities?.length ? (
            <section className="mb-4">
              <h3 className="mb-1 text-[12px] font-semibold text-ink-soft">핵심 정보</h3>
              <div className="flex flex-wrap gap-1">
                {doc.key_entities.map((e, i) => (
                  <span key={i} className="rounded bg-gray-100 px-1.5 py-0.5 text-[11.5px]">
                    {e}
                  </span>
                ))}
              </div>
            </section>
          ) : null}

          {doc.suggested_questions?.length ? (
            <section className="mb-4">
              <h3 className="mb-1 text-[12px] font-semibold text-ink-soft">추천 질문</h3>
              <ul className="space-y-1">
                {doc.suggested_questions.map((q, i) => (
                  <li key={i} className="text-ink-soft">
                    · {q}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          <div className="flex gap-2 border-t border-line pt-3">
            <a
              href={`/api/proxy/documents/${doc.id}/original`}
              className="rounded-lg border border-line px-2.5 py-1.5 text-[12.5px] hover:bg-gray-50"
            >
              원본 내려받기
            </a>
            {doc.status === "failed" && (
              <button
                onClick={async () => {
                  await fetch(`/api/proxy/documents/${doc.id}/reingest`, { method: "POST" });
                  onClose();
                }}
                className="rounded-lg border border-line px-2.5 py-1.5 text-[12.5px] hover:bg-gray-50"
              >
                다시 처리
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
