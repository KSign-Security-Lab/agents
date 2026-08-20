"use client";

import { useState } from "react";
import type { Step } from "@/lib/types";

const LABEL: Record<string, string> = {
  plan: "질문 분해",
  research: "검색",
  researcher: "하위 질의",
  merge: "근거 선정",
  tables: "표 계산",
  compose: "답변 작성",
  verify: "근거 검증",
};

/**
 * What the agent is doing right now.
 *
 * Shown while a turn runs because multi-hop retrieval takes ten seconds or more,
 * and a silent spinner gives no way to tell a slow answer from a stuck one.
 */
export default function StepsPanel({ steps }: { steps: Step[] }) {
  const [open, setOpen] = useState(true);
  const latest = steps[steps.length - 1];

  return (
    <div className="rounded-lg border border-line bg-gray-50 text-[12.5px]">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent animate-pulse-soft" />
        <span className="truncate text-ink-soft">
          {latest ? `${LABEL[latest.node] ?? latest.node} · ${latest.label}` : "시작하는 중…"}
        </span>
        <span className="ml-auto shrink-0 text-ink-muted">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <ol className="border-t border-line px-3 py-2">
          {steps.map((s) => (
            <li key={s.ord} className="flex gap-2 py-0.5 text-ink-muted">
              <span className="w-4 shrink-0 tabular-nums">{s.ord}.</span>
              <span className="w-20 shrink-0 text-ink-soft">{LABEL[s.node] ?? s.node}</span>
              <span className="truncate">{s.label}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
