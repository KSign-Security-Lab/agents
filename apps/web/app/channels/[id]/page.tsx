import { notFound, redirect } from "next/navigation";
import Shell from "@/components/Shell";
import Chat from "@/components/Chat";
import ChannelHeader from "@/components/ChannelHeader";
import RecordLastChannel from "@/components/RecordLastChannel";
import { api, apiSafe } from "@/lib/api";
import { getUser } from "@/lib/session";
import type { Channel, Doc, Message } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ChannelPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const user = await getUser();
  if (!user) redirect("/login");

  const channel = await apiSafe<Channel>(`/channels/${id}`);
  if (!channel) notFound();

  const [messages, docIds, allDocs] = await Promise.all([
    api<Message[]>(`/channels/${id}/messages`),
    api<string[]>(`/channels/${id}/documents`),
    api<Doc[]>("/documents"),
  ]);

  return (
    <Shell user={user} activeChannelId={id}>
      <RecordLastChannel id={id} />
      <ChannelHeader channel={channel} allDocuments={allDocs} attachedDocIds={docIds} />
      <div className="min-h-0 flex-1">
        <Chat channelId={id} initialMessages={messages} documents={allDocs} me={user} />
      </div>
    </Shell>
  );
}
