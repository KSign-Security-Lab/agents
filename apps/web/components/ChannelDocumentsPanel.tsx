"use client";

import { useCallback, useState } from "react";
import DocumentPicker from "./DocumentPicker";
import type { Doc } from "@/lib/types";

/** The "edit documents mid-channel" UI — the backend already supports
 *  PATCH .../documents, but no UI exposed it before this redesign. */
export default function ChannelDocumentsPanel({
  channelId,
  attachedDocs,
  onClose,
  onChange,
}: {
  channelId: string;
  attachedDocs: Doc[];
  onClose: () => void;
  onChange: () => void;
}) {
  const [adding, setAdding] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);

  const patchDocuments = useCallback(
    async (body: { add?: string[]; remove?: string[] }) => {
      setBusy(true);
      await fetch(`/api/proxy/channels/${channelId}/documents`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      }).catch(() => {});
      setBusy(false);
      onChange();
    },
    [channelId, onChange],
  );

  const remove = (docId: string) => void patchDocuments({ remove: [docId] });

  const addPicked = () => {
    if (adding.size === 0) return;
    void patchDocuments({ add: [...adding] }).then(() => setAdding(new Set()));
  };

  const attachedIds = new Set(attachedDocs.map((d) => d.id));

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/20" onClick={onClose}>
      <div
        className="flex h-full w-[420px] flex-col bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between border-b border-line px-4 py-3">
          <h2 className="text-[14px] font-semibold">문서 관리</h2>
          <button onClick={onClose} className="rounded px-2 py-1 text-ink-muted hover:bg-gray-100">
            ✕
          </button>
        </header>

        <div className="scrollbar-thin flex-1 overflow-y-auto p-4">
          <section className="mb-5">
            <h3 className="mb-1.5 text-[12px] font-semibold text-ink-soft">
              연결된 문서 {attachedDocs.length}
            </h3>
            {attachedDocs.length === 0 && (
              <p className="text-[12.5px] text-ink-muted">아직 연결된 문서가 없습니다.</p>
            )}
            <ul className="space-y-1">
              {attachedDocs.map((d) => (
                <li
                  key={d.id}
                  className="flex items-center gap-2 rounded border border-line px-2.5 py-1.5"
                >
                  <span className="min-w-0 flex-1 truncate text-[13px]">{d.filename}</span>
                  <button
                    onClick={() => remove(d.id)}
                    disabled={busy}
                    className="shrink-0 text-[11.5px] text-red-600 hover:underline disabled:opacity-40"
                  >
                    ✕ 제거
                  </button>
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h3 className="mb-1.5 text-[12px] font-semibold text-ink-soft">문서 추가</h3>
            <DocumentPicker
              excludeIds={attachedIds}
              selected={adding}
              onToggle={(id) =>
                setAdding((prev) => {
                  const next = new Set(prev);
                  next.has(id) ? next.delete(id) : next.add(id);
                  return next;
                })
              }
              onUploaded={(doc) => void patchDocuments({ add: [doc.id] })}
            />
            <button
              onClick={addPicked}
              disabled={busy || adding.size === 0}
              className="mt-2 w-full rounded-lg bg-accent px-3 py-1.5 text-[13px] font-medium text-white disabled:opacity-40"
            >
              선택한 문서 추가 {adding.size > 0 && `(${adding.size})`}
            </button>
          </section>
        </div>
      </div>
    </div>
  );
}
