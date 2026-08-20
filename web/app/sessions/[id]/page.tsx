import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import Chat from "@/components/Chat";
import { api, apiSafe } from "@/lib/api";
import { getUser } from "@/lib/session";
import type { ChatSession, Doc, Message } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function SessionPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const user = await getUser();
  if (!user) redirect("/login");

  const session = await apiSafe<ChatSession>(`/sessions/${id}`);
  if (!session) notFound();

  const [messages, scopeIds, allDocs] = await Promise.all([
    api<Message[]>(`/sessions/${id}/messages`),
    api<string[]>(`/sessions/${id}/documents`),
    api<Doc[]>("/documents"),
  ]);

  const inScope = new Set(scopeIds);
  // The viewer needs metadata for any cited document, which can include one
  // outside the session's scope when the agent escalated to the whole corpus.
  const documents = allDocs.filter((d) => inScope.has(d.id));
  const scoped = documents.length ? documents : allDocs.filter((d) => inScope.has(d.id));

  return (
    <div className="flex h-screen flex-col">
      <header className="flex shrink-0 items-center gap-3 border-b border-line px-4 py-2">
        <Link href="/" className="text-[13px] text-ink-muted hover:text-ink">
          ←
        </Link>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-[14px] font-semibold">{session.title}</h1>
          <p className="truncate text-[11.5px] text-ink-muted">
            {session.created_by?.name} 님이 시작 · 문서 {scopeIds.length}개
            {session.folder_name ? ` · 📁 ${session.folder_name}` : ""}
          </p>
        </div>
        <details className="relative">
          <summary className="cursor-pointer list-none rounded px-2 py-1 text-[12px] text-ink-muted hover:bg-gray-100">
            근거 문서 {scopeIds.length}
          </summary>
          <div className="absolute right-0 z-30 mt-1 w-80 rounded-lg border border-line bg-white p-2 shadow-lg">
            {scoped.map((d) => (
              <div key={d.id} className="truncate px-1.5 py-1 text-[12.5px]">
                {d.filename}
              </div>
            ))}
          </div>
        </details>
      </header>

      <div className="min-h-0 flex-1">
        <Chat
          sessionId={id}
          initialMessages={messages}
          documents={allDocs}
          me={user}
        />
      </div>
    </div>
  );
}
