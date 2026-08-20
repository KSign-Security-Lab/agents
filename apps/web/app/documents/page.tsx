import { redirect } from "next/navigation";
import Shell from "@/components/Shell";
import DocumentBrowser from "@/components/DocumentBrowser";
import { api } from "@/lib/api";
import { getUser } from "@/lib/session";
import type { Doc, Topic } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function DocumentsPage() {
  const user = await getUser();
  if (!user) redirect("/login");

  const [documents, topics] = await Promise.all([
    api<Doc[]>("/documents"),
    api<Topic[]>("/topics"),
  ]);

  return (
    <Shell user={user} activeTop="documents">
      <DocumentBrowser initialDocuments={documents} topics={topics} />
    </Shell>
  );
}
