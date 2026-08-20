"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import NewChannelModal from "./NewChannelModal";

/** Thin client wrapper so the server-component root page can reuse
 *  NewChannelModal (which needs client state) without duplicating it. */
export default function ChannelEmptyState() {
  const router = useRouter();
  const [open, setOpen] = useState(false);

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3">
      <p className="text-[14px] text-ink-soft">아직 채널이 없습니다.</p>
      <button
        onClick={() => setOpen(true)}
        className="rounded-lg bg-accent px-4 py-2 text-[13px] font-medium text-white"
      >
        채널 만들기
      </button>
      {open && (
        <NewChannelModal
          onClose={() => setOpen(false)}
          onCreated={(channel) => router.push(`/channels/${channel.id}`)}
        />
      )}
    </div>
  );
}
