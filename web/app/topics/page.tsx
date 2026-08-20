import { redirect } from "next/navigation";
import Shell from "@/components/Shell";
import TopicManager from "@/components/TopicManager";
import { api } from "@/lib/api";
import { getUser } from "@/lib/session";
import type { Topic } from "@/lib/types";

export const dynamic = "force-dynamic";

type Candidate = {
  similarity: number;
  suggested_keep: { id: string; name: string; doc_count: number };
  suggested_drop: { id: string; name: string; doc_count: number };
};

export default async function TopicsPage() {
  const user = await getUser();
  if (!user) redirect("/login");

  const [topics, candidates] = await Promise.all([
    api<Topic[]>("/topics"),
    api<Candidate[]>("/topics/merge-candidates"),
  ]);

  return (
    <Shell user={user} active="topics">
      <TopicManager
        topics={topics}
        candidates={candidates}
        isAdmin={user.role === "admin"}
      />
    </Shell>
  );
}
