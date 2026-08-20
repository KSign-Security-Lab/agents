import { apiBase, getToken } from "./session";

/** Server-side API calls. Throws on non-2xx so pages can fail loudly. */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await getToken();
  const res = await fetch(`${apiBase()}${path}`, {
    ...init,
    headers: {
      ...(init.body && !(init.body instanceof FormData)
        ? { "content-type": "application/json" }
        : {}),
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${res.status} ${path}: ${detail.slice(0, 400)}`);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

/** Same, but returns null instead of throwing — for optional data. */
export async function apiSafe<T>(path: string, init: RequestInit = {}): Promise<T | null> {
  try {
    return await api<T>(path, init);
  } catch {
    return null;
  }
}
