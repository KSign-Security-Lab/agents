import Link from "next/link";
import { redirect } from "next/navigation";
import Shell from "@/components/Shell";
import NewSessionButton from "@/components/NewSessionButton";
import { api } from "@/lib/api";
import { getUser } from "@/lib/session";
import type { ChatSession, Doc, Folder } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function SessionsPage() {
  const user = await getUser();
  if (!user) redirect("/login");

  const [sessions, documents, folders] = await Promise.all([
    api<ChatSession[]>("/sessions"),
    api<Doc[]>("/documents?status=ready"),
    api<Folder[]>("/folders"),
  ]);

  return (
    <Shell user={user} active="sessions">
      <header className="flex shrink-0 items-center justify-between border-b border-line px-5 py-3">
        <div>
          <h1 className="text-[15px] font-semibold">대화</h1>
          <p className="text-[12px] text-ink-muted">
            팀의 모든 대화가 보입니다 · {sessions.length}개
          </p>
        </div>
        <NewSessionButton documents={documents} folders={folders} />
      </header>

      <div className="scrollbar-thin flex-1 overflow-y-auto p-5">
        {sessions.length === 0 ? (
          <p className="text-[13px] text-ink-muted">
            아직 대화가 없습니다. 문서를 선택해 새 대화를 시작하세요.
          </p>
        ) : (
          <ul className="mx-auto max-w-3xl divide-y divide-line">
            {sessions.map((s) => (
              <li key={s.id}>
                <Link
                  href={`/sessions/${s.id}`}
                  className="flex items-baseline gap-3 py-3 hover:bg-gray-50"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[14px] font-medium">{s.title}</span>
                    <span className="mt-0.5 block text-[12px] text-ink-muted">
                      {s.created_by?.name ?? "알 수 없음"} · 메시지 {s.message_count}
                      {" · "}문서 {s.document_count}
                      {s.folder_name ? ` · 📁 ${s.folder_name}` : ""}
                    </span>
                  </span>
                  <span className="shrink-0 text-[11px] text-ink-muted">
                    {new Date(s.updated_at).toLocaleDateString("ko-KR")}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Shell>
  );
}
