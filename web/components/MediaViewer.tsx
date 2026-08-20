"use client";

import { useEffect, useRef, useState } from "react";
import type { Citation } from "@/lib/types";

type Segment = { text: string; start_ms: number | null; end_ms: number | null };

function timecode(ms: number): string {
  const t = Math.floor(ms / 1000);
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}

/**
 * Player plus transcript for audio and video sources.
 *
 * A citation into a recording is a time range, so "opening the source" means
 * seeking the player to the quoted moment and highlighting that stretch of the
 * transcript — the equivalent of a bbox on a page.
 */
export default function MediaViewer({
  documentId,
  kind,
  citation,
  className,
}: {
  documentId: string;
  kind: "audio" | "video";
  citation: Citation | null;
  className?: string;
}) {
  const [segments, setSegments] = useState<Segment[]>([]);
  const [nowMs, setNowMs] = useState(0);
  const mediaRef = useRef<HTMLVideoElement | HTMLAudioElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch(`/api/proxy/documents/${documentId}/transcript`)
      .then((r) => (r.ok ? r.json() : []))
      .then(setSegments)
      .catch(() => setSegments([]));
  }, [documentId]);

  // Seek when a citation is opened.
  useEffect(() => {
    if (!citation?.t_start_ms || !mediaRef.current) return;
    mediaRef.current.currentTime = citation.t_start_ms / 1000;
    void mediaRef.current.play().catch(() => {
      /* autoplay may be blocked; the seek still happened */
    });
  }, [citation?.idx, citation?.t_start_ms]);

  // Keep the active transcript line in view as playback advances.
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>("[data-active='true']");
    el?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [nowMs]);

  const src = `/api/proxy/documents/${documentId}/media`;
  const inCitation = (s: Segment) =>
    citation?.t_start_ms != null &&
    citation?.t_end_ms != null &&
    s.start_ms != null &&
    s.start_ms < citation.t_end_ms &&
    (s.end_ms ?? s.start_ms) > citation.t_start_ms;

  return (
    <div className={`flex h-full flex-col ${className ?? ""}`}>
      <div className="shrink-0 border-b border-line bg-black/90 p-2">
        {kind === "video" ? (
          <video
            ref={mediaRef as React.RefObject<HTMLVideoElement>}
            src={src}
            controls
            className="mx-auto max-h-[42vh] w-full"
            onTimeUpdate={(e) => setNowMs(e.currentTarget.currentTime * 1000)}
          />
        ) : (
          <audio
            ref={mediaRef as React.RefObject<HTMLAudioElement>}
            src={src}
            controls
            className="w-full"
            onTimeUpdate={(e) => setNowMs(e.currentTarget.currentTime * 1000)}
          />
        )}
      </div>

      <div ref={listRef} className="scrollbar-thin flex-1 overflow-auto p-3">
        {segments.length === 0 && (
          <p className="text-[13px] text-ink-muted">전사 결과가 없습니다.</p>
        )}
        {segments.map((s, i) => {
          const active =
            s.start_ms != null && nowMs >= s.start_ms && nowMs < (s.end_ms ?? s.start_ms + 3000);
          const cited = inCitation(s);
          return (
            <button
              key={i}
              data-active={active}
              onClick={() => {
                if (mediaRef.current && s.start_ms != null) {
                  mediaRef.current.currentTime = s.start_ms / 1000;
                }
              }}
              className={`mb-1 flex w-full gap-2 rounded px-2 py-1 text-left text-[13px] leading-relaxed transition-colors ${
                cited
                  ? "bg-amber-100 ring-1 ring-amber-400"
                  : active
                    ? "bg-accent-soft"
                    : "hover:bg-gray-50"
              }`}
            >
              <span className="shrink-0 tabular-nums text-[11px] text-ink-muted">
                {s.start_ms != null ? timecode(s.start_ms) : ""}
              </span>
              <span className="text-ink-soft">{s.text}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
