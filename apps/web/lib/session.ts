import { cookies } from "next/headers";

/**
 * The browser never sees the API. It holds an httpOnly cookie containing the
 * internal token, and every call goes through this tier, which attaches the
 * token server-side. That keeps the API stateless and keeps the token out of
 * any client-side JavaScript.
 */
export const SESSION_COOKIE = "agents_session";

export type SessionUser = {
  id: string;
  email: string;
  name: string;
  role: "admin" | "member";
};

export async function getToken(): Promise<string | null> {
  const jar = await cookies();
  return jar.get(SESSION_COOKIE)?.value ?? null;
}

export async function getUser(): Promise<SessionUser | null> {
  const token = await getToken();
  if (!token) return null;
  try {
    // The token is verified by the API on every request; here we only need the
    // display fields, so the payload is decoded rather than verified.
    const [, payload] = token.split(".");
    const claims = JSON.parse(
      Buffer.from(payload.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf8"),
    );
    if (typeof claims.exp === "number" && claims.exp * 1000 < Date.now()) return null;
    return {
      id: claims.sub,
      email: claims.email,
      name: claims.name ?? claims.email,
      role: claims.role ?? "member",
    };
  } catch {
    return null;
  }
}

export function apiBase(): string {
  return process.env.API_INTERNAL_URL ?? "http://api:8000";
}
