"use client";

import { useRef, useState } from "react";
import clsx from "clsx";
import type { Citation } from "@/lib/types";

function timecode(ms: number): string {
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}

export function citationLocator(c: Citation): string {
  if (c.page_no != null) return `p.${c.page_no}`;
  if (c.t_start_ms != null) return timecode(c.t_start_ms);
  return "";
}

/**
 * An inline reference marker.
 *
 * Hovering previews the source without leaving the answer — which is what makes
 * a cited answer quick to trust. Clicking opens the document at the exact place.
 */
export default function CitationPill({
  citation,
  onOpen,
}: {
  citation: Citation;
  onOpen: (c: Citation) => void;
}) {
  const [open, setOpen] = useState(false);
  const [above, setAbove] = useState(false);
  const ref = useRef<HTMLButtonElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const show = () => {
    if (timer.current) clearTimeout(timer.current);
    // Flip the card above the pill when there is no room below it.
    const rect = ref.current?.getBoundingClientRect();
    if (rect) setAbove(window.innerHeight - rect.bottom < 260);
    setOpen(true);
  };
  const hide = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setOpen(false), 120);
  };

  const locator = citationLocator(citation);

  return (
    <span className="relative inline-block align-baseline">
      <button
        ref={ref}
        type="button"
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        onClick={() => onOpen(citation)}
        aria-label={`근거 ${citation.idx}: ${citation.filename} ${locator}`}
        className={clsx(
          "mx-[2px] inline-flex h-[18px] min-w-[18px] items-center justify-center",
          "rounded-[5px] px-[5px] align-[1px] text-[11px] font-semibold leading-none",
          "transition-colors",
          citation.out_of_scope
            ? "bg-amber-100 text-amber-800 hover:bg-amber-200"
            : "bg-accent-soft text-accent hover:bg-blue-100",
        )}
      >
        {citation.idx}
      </button>

      {open && (
        <span
          role="tooltip"
          onMouseEnter={show}
          onMouseLeave={hide}
          className={clsx(
            "absolute z-40 w-[340px] cursor-pointer rounded-lg border border-line",
            "bg-white p-3 text-left shadow-lg",
            above ? "bottom-full mb-2" : "top-full mt-2",
            "left-0",
          )}
          onClick={() => onOpen(citation)}
        >
          <span className="mb-1 flex items-baseline gap-1.5">
            <span className="truncate text-[12px] font-semibold text-ink">
              {citation.filename}
            </span>
            {locator && (
              <span className="shrink-0 text-[11px] text-ink-muted">· {locator}</span>
            )}
          </span>
          {citation.heading_path && (
            <span className="mb-1.5 block truncate text-[11px] text-ink-muted">
              {citation.heading_path}
            </span>
          )}
          <span className="block border-l-2 border-cite pl-2 text-[12.5px] leading-relaxed text-ink-soft">
            {citation.snippet || "(본문 미리보기 없음)"}
          </span>
          {citation.out_of_scope && (
            <span className="mt-2 block text-[11px] text-amber-700">
              ⚠ 이 세션에서 선택하지 않은 문서입니다
            </span>
          )}
          <span className="mt-2 block text-[11px] text-accent">클릭하면 원문에서 확인합니다 →</span>
        </span>
      )}
    </span>
  );
}
