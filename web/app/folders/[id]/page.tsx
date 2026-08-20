import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import Shell from "@/components/Shell";
import { api, apiSafe } from "@/lib/api";
import { getUser } from "@/lib/session";
import type { ChatSession, Doc, Folder } from "@/lib/types";

export const dynamic = "force-dynamic";

/** A folder opened as a project: its documents and the conversations about them. */
export default async function FolderPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const user = await getUser();
  if (!user) redirect("/login");

  const folder = await apiSafe<Folder>(`/folders/${id}`);
  if (!folder) notFound();

  const [docIds, sessions, allDocs] = await Promise.all([
    api<string[]>(`/folders/${id}/documents`),
    api<ChatSession[]>(`/sessions?folder_id=${id}`),
    api<Doc[]>("/documents"),
  ]);
  const inFolder = new Set(docIds);
  const documents = allDocs.filter((d) => inFolder.has(d.id));

  return (
    <Shell user={user} active="folders">
      <header className="shrink-0 border-b border-line px-5 py-3">
        <Link href="/folders" className="text-[12px] text-ink-muted hover:text-ink">
          ← 폴더
        </Link>
        <h1 className="mt-1 text-[15px] font-semibold">📁 {folder.name}</h1>
        {folder.description && (
          <p className="text-[12px] text-ink-muted">{folder.description}</p>
        )}
      </header>

      <div className="scrollbar-thin flex-1 overflow-y-auto p-5">
        <div className="mx-auto grid max-w-4xl gap-6 md:grid-cols-2">
          <section>
            <h2 className="mb-2 text-[13px] font-semibold text-ink-soft">
              문서 {documents.length}
            </h2>
            <ul className="space-y-1.5">
              {documents.map((d) => (
                <li key={d.id} className="rounded-lg border border-line px-3 py-2">
                  <p className="truncate text-[13px]">{d.filename}</p>
                  <p className="text-[11px] text-ink-muted">
                    {d.topics.map((t) => t.name).join(", ") || "주제 없음"}
                  </p>
                </li>
              ))}
              {documents.length === 0 && (
                <li className="text-[12.5px] text-ink-muted">문서가 없습니다.</li>
              )}
            </ul>
          </section>

          <section>
            <h2 className="mb-2 text-[13px] font-semibold text-ink-soft">
              이 폴더의 대화 {sessions.length}
            </h2>
            <ul className="space-y-1.5">
              {sessions.map((s) => (
                <li key={s.id}>
                  <Link
                    href={`/sessions/${s.id}`}
                    className="block rounded-lg border border-line px-3 py-2 hover:border-accent"
                  >
                    <p className="truncate text-[13px]">{s.title}</p>
                    <p className="text-[11px] text-ink-muted">
                      {s.created_by?.name} · 메시지 {s.message_count}
                    </p>
                  </Link>
                </li>
              ))}
              {sessions.length === 0 && (
                <li className="text-[12.5px] text-ink-muted">아직 대화가 없습니다.</li>
              )}
            </ul>
          </section>
        </div>
      </div>
    </Shell>
  );
}
