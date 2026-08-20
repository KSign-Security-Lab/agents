"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Doc } from "@/lib/types";

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

/**
 * Existing-document checklist + drag-and-drop upload, shared by
 * NewChannelModal and ChannelDocumentsPanel so neither duplicates
 * DocumentBrowser's upload mechanics.
 */
export default function DocumentPicker({
  excludeIds,
  selected,
  onToggle,
  onUploaded,
}: {
  excludeIds?: Set<string>;
  selected: Set<string>;
  onToggle: (id: string) => void;
  onUploaded: (doc: Doc) => void;
}) {
  const [ready, setReady] = useState<Doc[]>([]);
  const [inFlight, setInFlight] = useState<Doc[]>([]);
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetch("/api/proxy/documents?status=ready")
      .then((r) => (r.ok ? r.json() : []))
      .then(setReady)
      .catch(() => {});
  }, []);

  // Reflect ingest-stage progress for anything uploaded through this picker.
  useEffect(() => {
    if (inFlight.length === 0) return;
    const es = new EventSource("/api/proxy/events");
    const onChange = () => {
      void Promise.all(
        inFlight.map((d) =>
          fetch(`/api/proxy/documents/${d.id}`).then((r) => (r.ok ? r.json() : d)),
        ),
      ).then((updated: Doc[]) => {
        setInFlight(updated.filter((d) => d.status !== "ready"));
        const justReady = updated.filter((d) => d.status === "ready");
        if (justReady.length) setReady((prev) => [...justReady, ...prev]);
      });
    };
    es.addEventListener("document.status", onChange);
    return () => es.close();
  }, [inFlight]);

  const upload = useCallback(
    async (files: FileList | File[]) => {
      for (const file of Array.from(files)) {
        const body = new FormData();
        body.append("file", file);
        const res = await fetch("/api/proxy/documents", { method: "POST", body }).catch(
          () => null,
        );
        if (res?.ok) {
          const doc: Doc = await res.json();
          onUploaded(doc);
          if (doc.status === "ready") setReady((prev) => [doc, ...prev]);
          else setInFlight((prev) => [...prev, doc]);
        }
      }
    },
    [onUploaded],
  );

  const excluded = excludeIds ?? new Set<string>();
  const filtered = useMemo(() => {
    let out = ready.filter((d) => !excluded.has(d.id));
    if (query.trim()) {
      const q = query.trim().toLowerCase();
      out = out.filter((d) => d.filename.toLowerCase().includes(q));
    }
    return out;
  }, [ready, excluded, query]);

  return (
    <div>
      <div
        className="mb-2 rounded-lg border-2 border-dashed border-line px-3 py-3 text-center"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          if (e.dataTransfer.files.length) void upload(e.dataTransfer.files);
        }}
      >
        <button
          onClick={() => inputRef.current?.click()}
          className="text-[12.5px] text-accent hover:underline"
        >
          파일 업로드
        </button>
        <span className="text-[12px] text-ink-muted"> 또는 끌어다 놓기</span>
        <input
          ref={inputRef}
          type="file"
          multiple
          hidden
          onChange={(e) => e.target.files && void upload(e.target.files)}
        />
      </div>

      {inFlight.length > 0 && (
        <div className="mb-2 space-y-1">
          {inFlight.map((d) => (
            <div
              key={d.id}
              className="flex items-center gap-1.5 rounded border border-accent bg-accent-soft px-2 py-1 text-[12px] text-accent"
            >
              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent animate-pulse-soft" />
              <span className="min-w-0 flex-1 truncate">{d.filename}</span>
              <span className="shrink-0">{STAGE_LABEL[d.status] ?? d.status}</span>
            </div>
          ))}
        </div>
      )}

      {ready.length > 8 && (
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="파일명 검색"
          className="mb-2 w-full rounded-lg border border-line px-2.5 py-1.5 text-[12.5px] outline-none focus:border-accent"
        />
      )}

      <div className="scrollbar-thin max-h-52 overflow-y-auto">
        {filtered.length === 0 && ready.length === 0 && (
          <p className="px-1.5 py-1 text-[12.5px] text-ink-muted">
            준비된 문서가 없습니다. 위에서 업로드하세요.
          </p>
        )}
        {filtered.map((d) => (
          <label
            key={d.id}
            className="flex cursor-pointer items-start gap-2 rounded px-1.5 py-1.5 hover:bg-gray-50"
          >
            <input
              type="checkbox"
              checked={selected.has(d.id)}
              onChange={() => onToggle(d.id)}
              className="mt-0.5"
            />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px]">{d.filename}</span>
              <span className="block truncate text-[11px] text-ink-muted">
                {d.topics.map((t) => t.name).join(", ") || "주제 없음"}
              </span>
            </span>
          </label>
        ))}
      </div>
    </div>
  );
}
