"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (res.ok) {
      router.push("/");
      router.refresh();
    } else {
      const data = await res.json().catch(() => ({}));
      setError(data.error ?? "로그인에 실패했습니다");
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
      <form onSubmit={submit} className="w-full max-w-[340px]">
        <h1 className="text-[18px] font-semibold">문서 에이전트</h1>
        <p className="mb-6 mt-1 text-[13px] text-ink-muted">
          업로드한 문서를 근거로 답변합니다
        </p>

        <label className="mb-1 block text-[12px] font-medium text-ink-soft">이메일</label>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          autoFocus
          className="mb-3 w-full rounded-lg border border-line px-3 py-2 text-[14px] outline-none focus:border-accent"
        />

        <label className="mb-1 block text-[12px] font-medium text-ink-soft">비밀번호</label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          className="mb-4 w-full rounded-lg border border-line px-3 py-2 text-[14px] outline-none focus:border-accent"
        />

        {error && (
          <p className="mb-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-[12.5px] text-red-700">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-lg bg-accent py-2.5 text-[14px] font-medium text-white disabled:opacity-50"
        >
          {busy ? "확인 중…" : "로그인"}
        </button>
      </form>
    </div>
  );
}
