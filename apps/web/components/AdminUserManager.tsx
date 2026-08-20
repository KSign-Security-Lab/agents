"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { AdminUser } from "@/lib/types";

/**
 * User management for admins. Role changes and activation both PATCH
 * /admin/users/{id} directly (no draft/save step — both fields are closed,
 * two-value choices, not free text). The backend only refuses to demote the
 * last admin's *role*; it has no equivalent guard for is_active, so the UI
 * refuses to let anyone deactivate their own row.
 */
export default function AdminUserManager({
  users,
  currentUserId,
}: {
  users: AdminUser[];
  currentUserId: string;
}) {
  const router = useRouter();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<{ id: string; message: string } | null>(null);
  const [open, setOpen] = useState(false);

  const adminCount = users.filter((u) => u.role === "admin").length;

  const patchUser = async (id: string, body: Record<string, unknown>) => {
    setBusyId(id);
    setRowError(null);
    const res = await fetch(`/api/proxy/admin/users/${id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    setBusyId(null);
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      setRowError({ id, message: data.detail ?? "요청에 실패했습니다" });
      return;
    }
    router.refresh();
  };

  return (
    <>
      <header className="flex shrink-0 items-center justify-between border-b border-line px-5 py-3">
        <div>
          <h1 className="text-[15px] font-semibold">관리자</h1>
          <p className="text-[12px] text-ink-muted">사용자 {users.length}명</p>
        </div>
        <button
          onClick={() => setOpen(true)}
          className="rounded-lg bg-accent px-3 py-1.5 text-[13px] font-medium text-white"
        >
          새 사용자
        </button>
      </header>

      <div className="scrollbar-thin flex-1 overflow-y-auto p-5">
        <div className="mx-auto max-w-3xl">
          <ul className="divide-y divide-line">
            {users.map((u) => {
              const isSelf = u.id === currentUserId;
              const isLastAdmin = u.role === "admin" && adminCount <= 1;
              const busy = busyId === u.id;
              return (
                <li key={u.id} className="flex flex-col gap-1 py-2.5">
                  <div className="flex items-center gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[13.5px]">{u.name}</p>
                      <p className="truncate text-[12px] text-ink-muted">{u.email}</p>
                    </div>
                    <select
                      value={u.role}
                      disabled={busy || isLastAdmin}
                      onChange={(e) => void patchUser(u.id, { role: e.target.value })}
                      className="rounded border border-line px-2 py-1 text-[12.5px] disabled:opacity-40"
                    >
                      <option value="member">일반</option>
                      <option value="admin">관리자</option>
                    </select>
                    <span
                      className={`text-[11.5px] ${
                        u.is_active ? "text-ink-muted" : "text-red-700"
                      }`}
                    >
                      {u.is_active ? "활성" : "비활성"}
                    </span>
                    <button
                      onClick={() => void patchUser(u.id, { is_active: !u.is_active })}
                      disabled={busy || isSelf}
                      className="text-[12px] text-accent hover:underline disabled:text-ink-muted disabled:no-underline"
                    >
                      {u.is_active ? "비활성화" : "재활성화"}
                    </button>
                  </div>
                  {rowError?.id === u.id && (
                    <p className="text-[12px] text-red-700">{rowError.message}</p>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      </div>

      {open && <CreateUserModal onClose={() => setOpen(false)} />}
    </>
  );
}

function CreateUserModal({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("member");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const create = async () => {
    setBusy(true);
    setError(null);
    const res = await fetch("/api/proxy/admin/users", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, name, password, role }),
    });
    setBusy(false);
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      setError(data.detail ?? "사용자를 만들지 못했습니다");
      return;
    }
    onClose();
    router.refresh();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
      <div className="flex max-h-[80vh] w-full max-w-lg flex-col rounded-xl bg-white shadow-xl">
        <div className="border-b border-line px-4 py-3">
          <h2 className="text-[14px] font-semibold">새 사용자</h2>
        </div>
        <div className="scrollbar-thin flex-1 overflow-y-auto px-4 py-3">
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="이메일"
            autoFocus
            className="mb-2 w-full rounded-lg border border-line px-2.5 py-2 text-[13px] outline-none focus:border-accent"
          />
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="이름"
            className="mb-2 w-full rounded-lg border border-line px-2.5 py-2 text-[13px] outline-none focus:border-accent"
          />
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="비밀번호 (8자 이상)"
            minLength={8}
            className="mb-2 w-full rounded-lg border border-line px-2.5 py-2 text-[13px] outline-none focus:border-accent"
          />
          <select
            value={role}
            onChange={(e) => setRole(e.target.value)}
            className="mb-3 w-full rounded-lg border border-line px-2.5 py-2 text-[13px]"
          >
            <option value="member">일반</option>
            <option value="admin">관리자</option>
          </select>
          {error && (
            <p className="rounded border border-red-200 bg-red-50 px-3 py-2 text-[12.5px] text-red-700">
              {error}
            </p>
          )}
        </div>
        <div className="flex justify-end gap-2 border-t border-line px-4 py-3">
          <button
            onClick={onClose}
            className="rounded-lg px-3 py-1.5 text-[13px] text-ink-soft hover:bg-gray-100"
          >
            취소
          </button>
          <button
            onClick={() => void create()}
            disabled={busy || !email.trim() || !name.trim() || password.length < 8}
            className="rounded-lg bg-accent px-3 py-1.5 text-[13px] font-medium text-white disabled:opacity-40"
          >
            만들기
          </button>
        </div>
      </div>
    </div>
  );
}
