import { redirect } from "next/navigation";
import Shell from "@/components/Shell";
import AdminUserManager from "@/components/AdminUserManager";
import { api } from "@/lib/api";
import { getUser } from "@/lib/session";
import type { AdminUser } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function AdminPage() {
  const user = await getUser();
  if (!user) redirect("/login");
  if (user.role !== "admin") redirect("/");

  const users = await api<AdminUser[]>("/admin/users");

  return (
    <Shell user={user} activeTop="admin">
      <AdminUserManager users={users} currentUserId={user.id} />
    </Shell>
  );
}
