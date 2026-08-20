import Link from "next/link";
import { redirect } from "next/navigation";
import Shell from "@/components/Shell";
import NewFolderButton from "@/components/NewFolderButton";
import { api } from "@/lib/api";
import { getUser } from "@/lib/session";
import type { Doc, Folder } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function FoldersPage() {
  const user = await getUser();
  if (!user) redirect("/login");

  const [folders, documents] = await Promise.all([
    api<Folder[]>("/folders"),
    api<Doc[]>("/documents?status=ready"),
  ]);

  return (
    <Shell user={user} active="folders">
      <header className="flex shrink-0 items-center justify-between border-b border-line px-5 py-3">
        <div>
          <h1 className="text-[15px] font-semibold">폴더</h1>
          <p className="text-[12px] text-ink-muted">
            문서 묶음이자 프로젝트 화면입니다 · {folders.length}개
          </p>
        </div>
        <NewFolderButton documents={documents} />
      </header>

      <div className="scrollbar-thin flex-1 overflow-y-auto p-5">
        {folders.length === 0 ? (
          <p className="text-[13px] text-ink-muted">
            폴더가 없습니다. 자주 함께 보는 문서를 묶어 두면 대화를 반복해서 열 수 있습니다.
          </p>
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-3">
            {folders.map((f) => (
              <Link
                key={f.id}
                href={`/folders/${f.id}`}
                className="rounded-xl border border-line p-4 hover:border-accent"
              >
                <p className="truncate text-[14px] font-medium">📁 {f.name}</p>
                {f.description && (
                  <p className="mt-1 line-clamp-2 text-[12px] text-ink-soft">{f.description}</p>
                )}
                <p className="mt-2 text-[11.5px] text-ink-muted">
                  문서 {f.document_count} · 대화 {f.session_count} · {f.created_by?.name}
                </p>
              </Link>
            ))}
          </div>
        )}
      </div>
    </Shell>
  );
}
