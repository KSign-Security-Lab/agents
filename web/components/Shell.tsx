import Link from "next/link";
import type { User } from "@/lib/types";

/** Left rail + page body. Sessions, documents, folders and topics are all
 *  workspace-wide, so there is no "my stuff" section. */
export default function Shell({
  user,
  active,
  children,
}: {
  user: User;
  active: "sessions" | "documents" | "folders" | "topics";
  children: React.ReactNode;
}) {
  const items = [
    { key: "sessions", href: "/", label: "대화", icon: "💬" },
    { key: "documents", href: "/documents", label: "문서", icon: "📄" },
    { key: "folders", href: "/folders", label: "폴더", icon: "📁" },
    { key: "topics", href: "/topics", label: "주제", icon: "🏷" },
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

        <div className="flex-1 px-2">
          {items.map((it) => (
            <Link
              key={it.key}
              href={it.href}
              className={`mb-0.5 flex items-center gap-2 rounded-md px-2.5 py-1.5 text-[13px] ${
                active === it.key
                  ? "bg-white font-medium text-ink shadow-sm"
                  : "text-ink-soft hover:bg-white/70"
              }`}
            >
              <span className="text-[13px]">{it.icon}</span>
              {it.label}
            </Link>
          ))}
        </div>

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
