"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { Topic } from "@/lib/types";

type Candidate = {
  similarity: number;
  suggested_keep: { id: string; name: string; doc_count: number };
  suggested_drop: { id: string; name: string; doc_count: number };
};

/**
 * The taxonomy is proposed by the agent, so this screen is where a person keeps
 * it honest: rename a clumsy label, and merge the near-duplicates the agent
 * inevitably produces when two documents are ingested at the same time.
 */
export default function TopicManager({
  topics,
  candidates,
  isAdmin,
}: {
  topics: Topic[];
  candidates: Candidate[];
  isAdmin: boolean;
}) {
  const router = useRouter();
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  const rename = async (id: string) => {
    setBusy(true);
    await fetch(`/api/proxy/topics/${id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name }),
    });
    setBusy(false);
    setEditing(null);
    router.refresh();
  };

  const merge = async (keep: string, drop: string) => {
    setBusy(true);
    await fetch(`/api/proxy/topics/merge?keep_id=${keep}&drop_id=${drop}`, { method: "POST" });
    setBusy(false);
    router.refresh();
  };

  return (
    <>
      <header className="shrink-0 border-b border-line px-5 py-3">
        <h1 className="text-[15px] font-semibold">주제</h1>
        <p className="text-[12px] text-ink-muted">
          업로드된 문서로부터 자동으로 만들어집니다 · {topics.length}개
        </p>
      </header>

      <div className="scrollbar-thin flex-1 overflow-y-auto p-5">
        <div className="mx-auto max-w-3xl">
          {candidates.length > 0 && (
            <section className="mb-6 rounded-xl border border-amber-200 bg-amber-50 p-3">
              <h2 className="text-[13px] font-semibold text-amber-900">
                합칠 만한 주제 {candidates.length}건
              </h2>
              <p className="mb-2 mt-0.5 text-[11.5px] text-amber-800">
                비슷하지만 완전히 같지는 않아 자동으로 합치지 않았습니다. 확인 후 결정하세요.
              </p>
              <ul className="space-y-1.5">
                {candidates.map((c, i) => (
                  <li
                    key={i}
                    className="flex items-center gap-2 rounded-lg bg-white px-2.5 py-2 text-[12.5px]"
                  >
                    <span className="tabular-nums text-ink-muted">
                      {(c.similarity * 100).toFixed(0)}%
                    </span>
                    <span className="font-medium">{c.suggested_drop.name}</span>
                    <span className="text-ink-muted">→</span>
                    <span className="font-medium">{c.suggested_keep.name}</span>
                    <span className="text-ink-muted">
                      ({c.suggested_keep.doc_count}개 문서)
                    </span>
                    {isAdmin && (
                      <button
                        onClick={() => void merge(c.suggested_keep.id, c.suggested_drop.id)}
                        disabled={busy}
                        className="ml-auto rounded bg-ink px-2 py-1 text-[11.5px] text-white disabled:opacity-40"
                      >
                        합치기
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <ul className="divide-y divide-line">
            {topics.map((t) => (
              <li key={t.id} className="flex items-center gap-3 py-2.5">
                {editing === t.id ? (
                  <>
                    <input
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      autoFocus
                      className="flex-1 rounded border border-line px-2 py-1 text-[13px]"
                    />
                    <button
                      onClick={() => void rename(t.id)}
                      disabled={busy}
                      className="rounded bg-accent px-2 py-1 text-[12px] text-white"
                    >
                      저장
                    </button>
                    <button
                      onClick={() => setEditing(null)}
                      className="text-[12px] text-ink-muted"
                    >
                      취소
                    </button>
                  </>
                ) : (
                  <>
                    <span className="flex-1 text-[13.5px]">{t.name}</span>
                    <span className="text-[12px] text-ink-muted">문서 {t.doc_count}</span>
                    {isAdmin && (
                      <button
                        onClick={() => {
                          setEditing(t.id);
                          setName(t.name);
                        }}
                        className="text-[12px] text-accent hover:underline"
                      >
                        이름 변경
                      </button>
                    )}
                  </>
                )}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </>
  );
}
