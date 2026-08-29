import "server-only";

import { cookies } from "next/headers";

import { API_URL, SESSION_COOKIE } from "@/lib/api";
import type { User } from "@/lib/types";

/**
 * Resolve the current user server-side.
 *
 * The cookie is set by the API on :8001, but it is host-only on `localhost`, so
 * the browser sends it to the Next server on :3001 too — we just forward it back.
 */
export async function getSession(): Promise<User | null> {
  const token = cookies().get(SESSION_COOKIE)?.value;
  if (!token) return null;

  try {
    const res = await fetch(`${API_URL}/api/auth/me`, {
      headers: { cookie: `${SESSION_COOKIE}=${token}` },
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as User;
  } catch {
    // API down — treat as signed out rather than crashing the page.
    return null;
  }
}
