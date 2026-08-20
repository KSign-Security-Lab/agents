"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import ChannelDocumentsPanel from "./ChannelDocumentsPanel";
import type { Channel, Doc } from "@/lib/types";

export default function ChannelHeader({
  channel,
  allDocuments,
  attachedDocIds,
}: {
  channel: Channel;
  allDocuments: Doc[];
  attachedDocIds: string[];
}) {
  const router = useRouter();
  const [panelOpen, setPanelOpen] = useState(false);
  const attachedIds = new Set(attachedDocIds);
  const attached = allDocuments.filter((d) => attachedIds.has(d.id));

  return (
    <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line px-4 py-2">
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-[14px] font-semibold"># {channel.name}</h1>
        {channel.description && (
          <p className="truncate text-[11.5px] text-ink-muted">{channel.description}</p>
        )}
      </div>
      <button
        onClick={() => setPanelOpen(true)}
        className="shrink-0 rounded px-2 py-1 text-[12px] text-ink-muted hover:bg-gray-100"
      >
        📎 문서 {attachedDocIds.length}
      </button>

      {panelOpen && (
        <ChannelDocumentsPanel
          channelId={channel.id}
          attachedDocs={attached}
          onClose={() => setPanelOpen(false)}
          onChange={() => router.refresh()}
        />
      )}
    </header>
  );
}
