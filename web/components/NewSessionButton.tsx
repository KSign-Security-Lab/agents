"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { Doc, Folder } from "@/lib/types";

/** Start a session against a folder or an ad-hoc selection of documents. */
export default function NewSessionButton({
  documents,
  folders,
}: {
  documents: Doc[];
  folders: Folder[];
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [folderId, setFolderId] = useState<string>("");
  const [busy, setBusy] = useState(false);

  const toggle = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const create = async () => {
    setBusy(true);
    const res = await fetch("/api/proxy/sessions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        folder_id: folderId || null,
        document_ids: [...picked],
      }),
    });
    if (res.ok) {
      const s = await res.json();
      router.push(`/sessions/${s.id}`);
    } else {
      setBusy(false);
    }
  };

  const count = picked.size + (folderId ? (folders.find((f) => f.id === folderId)?.document_count ?? 0) : 0);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="rounded-lg bg-accent px-3 py-1.5 text-[13px] font-medium text-white"
      >
        새 대화
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <div className="flex max-h-[80vh] w-full max-w-lg flex-col rounded-xl bg-white shadow-xl">
            <div className="border-b border-line px-4 py-3">
              <h2 className="text-[14px] font-semibold">새 대화</h2>
              <p className="mt-0.5 text-[12px] text-ink-muted">
                폴더를 고르거나 문서를 직접 선택하세요
              </p>
            </div>

            <div className="scrollbar-thin flex-1 overflow-y-auto px-4 py-3">
              {folders.length > 0 && (
                <>
                  <p className="mb-1.5 text-[12px] font-medium text-ink-soft">폴더</p>
                  <select
                    value={folderId}
                    onChange={(e) => setFolderId(e.target.value)}
                    className="mb-4 w-full rounded-lg border border-line px-2.5 py-2 text-[13px]"
                  >
                    <option value="">(선택 안 함)</option>
                    {folders.map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.name} · 문서 {f.document_count}
                      </option>
                    ))}
                  </select>
                </>
              )}

              <p className="mb-1.5 text-[12px] font-medium text-ink-soft">
                문서 직접 선택 {picked.size > 0 && `(${picked.size})`}
              </p>
              {documents.length === 0 && (
                <p className="text-[12.5px] text-ink-muted">
                  아직 준비된 문서가 없습니다.
                </p>
              )}
              {documents.map((d) => (
                <label
                  key={d.id}
                  className="flex cursor-pointer items-start gap-2 rounded px-1.5 py-1.5 hover:bg-gray-50"
                >
                  <input
                    type="checkbox"
                    checked={picked.has(d.id)}
                    onChange={() => toggle(d.id)}
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

            <div className="flex items-center justify-between border-t border-line px-4 py-3">
              <span className="text-[12px] text-ink-muted">
                {count > 0 ? `문서 ${count}개` : "문서를 선택하세요"}
              </span>
              <div className="flex gap-2">
                <button
                  onClick={() => setOpen(false)}
                  className="rounded-lg px-3 py-1.5 text-[13px] text-ink-soft hover:bg-gray-100"
                >
                  취소
                </button>
                <button
                  onClick={() => void create()}
                  disabled={busy || count === 0}
                  className="rounded-lg bg-accent px-3 py-1.5 text-[13px] font-medium text-white disabled:opacity-40"
                >
                  {busy ? "만드는 중…" : "시작"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
