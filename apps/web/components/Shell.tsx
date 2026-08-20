import Link from "next/link";
import ChannelSidebar from "./ChannelSidebar";
import { api } from "@/lib/api";
import type { Channel, User } from "@/lib/types";

/** Left rail + page body. Documents, topics and channels are all
 *  workspace-wide, so there is no "my stuff" section. Admin is the one
 *  exception — its icon only shows for users with the admin role. Channels
 *  are the primary navigable entity (Slack/Discord-style), so they get the
 *  bulk of the rail; documents/topics/admin are a small icon row above it. */
export default async function Shell({
  user,
  activeTop,
  activeChannelId,
  children,
}: {
  user: User;
  activeTop?: "documents" | "topics" | "admin";
  activeChannelId?: string;
  children: React.ReactNode;
}) {
  const channels = await api<Channel[]>("/channels");

  const topItems = [
    { key: "documents", href: "/documents", label: "문서", icon: "📄" },
    { key: "topics", href: "/topics", label: "주제", icon: "🏷" },
    ...(user.role === "admin"
      ? [{ key: "admin", href: "/admin", label: "관리자", icon: "🛠" } as const]
      : []),
  ] as const;

  return (
    <div className="flex h-screen overflow-hidden">
      <nav className="flex w-[196px] shrink-0 flex-col border-r border-line bg-gray-50">
        <div className="px-4 py-3">
          <Link href="/" className="text-[14px] font-semibold">
            문서 에이전트
          </Link>
          <p className="mt-0.5 text-[11px] text-ink-muted">공유 워크스페이스</p>
        </div>

        <div className="flex gap-1 px-2">
          {topItems.map((it) => (
            <Link
              key={it.key}
              href={it.href}
              title={it.label}
              className={`flex flex-1 flex-col items-center gap-0.5 rounded-md py-1.5 text-[10.5px] ${
                activeTop === it.key
                  ? "bg-white font-medium text-ink shadow-sm"
                  : "text-ink-soft hover:bg-white/70"
              }`}
            >
              <span className="text-[13px]">{it.icon}</span>
              {it.label}
            </Link>
          ))}
        </div>

        <div className="mx-3 my-2 border-t border-line" />

        <ChannelSidebar initialChannels={channels} activeChannelId={activeChannelId} />

        <div className="border-t border-line px-3 py-2.5">
          <p className="truncate text-[12px] font-medium">{user.name}</p>
          <p className="truncate text-[11px] text-ink-muted">{user.email}</p>
          <form action="/api/auth/logout" method="post">
            <button className="mt-1.5 text-[11px] text-ink-muted hover:text-ink">
              로그아웃
            </button>
          </form>
        </div>
      </nav>

      <main className="flex min-w-0 flex-1 flex-col">{children}</main>
    </div>
  );
}
