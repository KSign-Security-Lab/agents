import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, apiBase, getToken } from "@/lib/session";

/**
 * Browser -> API proxy.
 *
 * Everything the client calls goes through here so the internal token stays in
 * an httpOnly cookie. Streaming responses (SSE) are passed through untouched:
 * buffering them would defeat the point of streaming an answer token by token.
 */
export const dynamic = "force-dynamic";

const HOP_BY_HOP = new Set([
  "connection", "keep-alive", "transfer-encoding", "upgrade", "host",
  "content-length", "content-encoding",
]);

async function forward(req: NextRequest, path: string[]) {
  const token = await getToken();
  if (!token) {
    return NextResponse.json({ detail: "로그인이 필요합니다" }, { status: 401 });
  }

  const target = `${apiBase()}/${path.join("/")}${req.nextUrl.search}`;
  const headers = new Headers();
  req.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase()) && key.toLowerCase() !== "cookie") {
      headers.set(key, value);
    }
  });
  headers.set("authorization", `Bearer ${token}`);

  const hasBody = !["GET", "HEAD"].includes(req.method);
  const res = await fetch(target, {
    method: req.method,
    headers,
    body: hasBody ? req.body : undefined,
    // Required by undici whenever a streaming body is forwarded.
    ...(hasBody ? { duplex: "half" } : {}),
    redirect: "manual",
  } as RequestInit);

  const outHeaders = new Headers();
  res.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) outHeaders.set(key, value);
  });
  // Proxies and dev servers will otherwise chunk-buffer the event stream.
  if (outHeaders.get("content-type")?.includes("text/event-stream")) {
    outHeaders.set("cache-control", "no-cache, no-transform");
    outHeaders.set("x-accel-buffering", "no");
  }

  const out = new NextResponse(res.body, { status: res.status, headers: outHeaders });
  if (res.status === 401) out.cookies.set(SESSION_COOKIE, "", { path: "/", maxAge: 0 });
  return out;
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(req, (await ctx.params).path);
}
export async function POST(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(req, (await ctx.params).path);
}
export async function PATCH(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(req, (await ctx.params).path);
}
export async function PUT(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(req, (await ctx.params).path);
}
export async function DELETE(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(req, (await ctx.params).path);
}
