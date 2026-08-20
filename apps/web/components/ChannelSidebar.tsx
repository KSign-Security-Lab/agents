"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import clsx from "clsx";
import NewChannelModal from "./NewChannelModal";
import type { Channel } from "@/lib/types";

export default function ChannelSidebar({
  initialChannels,
  activeChannelId,
}: {
  initialChannels: Channel[];
  activeChannelId?: string;
}) {
  const router = useRouter();
  const [channels, setChannels] = useState(initialChannels);
  const [open, setOpen] = useState(false);

  const refresh = useCallback(async () => {
    const res = await fetch("/api/proxy/channels");
    if (res.ok) setChannels(await res.json());
  }, []);

  // Channel create/archive is workspace-wide, so anyone's new channel shows
  // up here live — same pattern DocumentBrowser uses for ingest progress.
  useEffect(() => {
    const es = new EventSource("/api/proxy/events");
    const onChange = () => void refresh();
    es.addEventListener("channel.created", onChange);
    es.addEventListener("channel.archived", onChange);
    return () => es.close();
  }, [refresh]);

  return (
    <div className="flex min-h-0 flex-1 flex-col px-2 pt-2">
      <div className="mb-1 flex items-center justify-between px-1.5">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-ink-muted">
          채널
        </span>
        <button
          onClick={() => setOpen(true)}
          className="rounded px-1.5 text-[14px] leading-none text-ink-muted hover:bg-white/70 hover:text-ink"
          title="채널 만들기"
        >
          +
        </button>
      </div>

      <div className="scrollbar-thin flex-1 overflow-y-auto">
        {channels.length === 0 ? (
          <p className="px-1.5 py-1 text-[12px] text-ink-muted">채널이 없습니다</p>
        ) : (
          channels.map((c) => (
            <Link
              key={c.id}
              href={`/channels/${c.id}`}
              className={clsx(
                "mb-0.5 flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[13px]",
                activeChannelId === c.id
                  ? "bg-white font-medium text-ink shadow-sm"
                  : "text-ink-soft hover:bg-white/70",
              )}
            >
              <span className="text-ink-muted">#</span>
              <span className="truncate">{c.name}</span>
            </Link>
          ))
        )}
      </div>

      {open && (
        <NewChannelModal
          onClose={() => setOpen(false)}
          onCreated={(channel) => {
            setOpen(false);
            setChannels((prev) => [channel, ...prev]);
            router.push(`/channels/${channel.id}`);
          }}
        />
      )}
    </div>
  );
}
