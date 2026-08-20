import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, apiBase } from "@/lib/session";

export async function POST(req: NextRequest) {
  const body = await req.json();
  const res = await fetch(`${apiBase()}/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: "로그인에 실패했습니다" }));
    return NextResponse.json({ error: detail.detail ?? "로그인에 실패했습니다" },
                             { status: res.status });
  }

  const data = await res.json();
  const out = NextResponse.json({ user: data.user });
  out.cookies.set(SESSION_COOKIE, data.token, {
    httpOnly: true,          // never readable by client-side script
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: data.expires_in,
  });
  return out;
}
