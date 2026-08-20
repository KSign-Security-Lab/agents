"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { Doc } from "@/lib/types";

export default function NewFolderButton({ documents }: { documents: Doc[] }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);

  const create = async () => {
    setBusy(true);
    const res = await fetch("/api/proxy/folders", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name, description: description || null, document_ids: [...picked] }),
    });
    setBusy(false);
    if (res.ok) {
      setOpen(false);
      setName("");
      setDescription("");
      setPicked(new Set());
      router.refresh();
    }
  };

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="rounded-lg bg-accent px-3 py-1.5 text-[13px] font-medium text-white"
      >
        새 폴더
      </button>
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <div className="flex max-h-[80vh] w-full max-w-lg flex-col rounded-xl bg-white shadow-xl">
            <div className="border-b border-line px-4 py-3">
              <h2 className="text-[14px] font-semibold">새 폴더</h2>
            </div>
            <div className="scrollbar-thin flex-1 overflow-y-auto px-4 py-3">
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="폴더 이름"
                autoFocus
                className="mb-2 w-full rounded-lg border border-line px-2.5 py-2 text-[13px] outline-none focus:border-accent"
              />
              <input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="설명 (선택)"
                className="mb-3 w-full rounded-lg border border-line px-2.5 py-2 text-[13px] outline-none focus:border-accent"
              />
              <p className="mb-1.5 text-[12px] font-medium text-ink-soft">
                문서 {picked.size > 0 && `(${picked.size})`}
              </p>
              {documents.map((d) => (
                <label
                  key={d.id}
                  className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 hover:bg-gray-50"
                >
                  <input
                    type="checkbox"
                    checked={picked.has(d.id)}
                    onChange={() =>
                      setPicked((prev) => {
                        const next = new Set(prev);
                        next.has(d.id) ? next.delete(d.id) : next.add(d.id);
                        return next;
                      })
                    }
                  />
                  <span className="truncate text-[13px]">{d.filename}</span>
                </label>
              ))}
            </div>
            <div className="flex justify-end gap-2 border-t border-line px-4 py-3">
              <button
                onClick={() => setOpen(false)}
                className="rounded-lg px-3 py-1.5 text-[13px] text-ink-soft hover:bg-gray-100"
              >
                취소
              </button>
              <button
                onClick={() => void create()}
                disabled={busy || !name.trim()}
                className="rounded-lg bg-accent px-3 py-1.5 text-[13px] font-medium text-white disabled:opacity-40"
              >
                만들기
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
