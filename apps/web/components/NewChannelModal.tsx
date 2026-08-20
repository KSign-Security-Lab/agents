"use client";

import { useState } from "react";
import DocumentPicker from "./DocumentPicker";
import type { Channel } from "@/lib/types";

export default function NewChannelModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (channel: Channel) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const create = async () => {
    setBusy(true);
    setError(null);
    const res = await fetch("/api/proxy/channels", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        name,
        description: description || null,
        document_ids: [...picked],
      }),
    });
    setBusy(false);
    if (res.ok) {
      onCreated(await res.json());
      return;
    }
    const data = await res.json().catch(() => ({}));
    setError(data.detail ?? "채널을 만들지 못했습니다");
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
      <div className="flex max-h-[85vh] w-full max-w-lg flex-col rounded-xl bg-white shadow-xl">
        <div className="border-b border-line px-4 py-3">
          <h2 className="text-[14px] font-semibold">새 채널</h2>
        </div>

        <div className="scrollbar-thin flex-1 overflow-y-auto px-4 py-3">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="채널 이름 (예: 2025-예산안)"
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
          <DocumentPicker
            selected={picked}
            onToggle={toggle}
            onUploaded={(doc) => setPicked((prev) => new Set(prev).add(doc.id))}
          />
          {error && (
            <p className="mt-2 rounded border border-red-200 bg-red-50 px-3 py-2 text-[12.5px] text-red-700">
              {error}
            </p>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-line px-4 py-3">
          <button
            onClick={onClose}
            className="rounded-lg px-3 py-1.5 text-[13px] text-ink-soft hover:bg-gray-100"
          >
            취소
          </button>
          <button
            onClick={() => void create()}
            disabled={busy || !name.trim()}
            className="rounded-lg bg-accent px-3 py-1.5 text-[13px] font-medium text-white disabled:opacity-40"
          >
            {busy ? "만드는 중…" : "만들기"}
          </button>
        </div>
      </div>
    </div>
  );
}
