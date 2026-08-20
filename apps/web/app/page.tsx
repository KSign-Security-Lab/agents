import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import Shell from "@/components/Shell";
import ChannelEmptyState from "@/components/ChannelEmptyState";
import { api } from "@/lib/api";
import { getUser } from "@/lib/session";
import type { Channel } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function RootPage() {
  const user = await getUser();
  if (!user) redirect("/login");

  const channels = await api<Channel[]>("/channels");

  const lastId = (await cookies()).get("last_channel_id")?.value;
  if (lastId && channels.some((c) => c.id === lastId)) {
    redirect(`/channels/${lastId}`);
  }
  if (channels.length > 0) {
    redirect(`/channels/${channels[0].id}`);
  }

  return (
    <Shell user={user}>
      <ChannelEmptyState />
    </Shell>
  );
}
