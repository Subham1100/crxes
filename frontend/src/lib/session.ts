import "server-only";

import { cookies } from "next/headers";

import { API_URL, SESSION_COOKIE } from "@/lib/api";
import type { User } from "@/lib/types";

/**
 * Resolve the current user server-side.
 *
 * The cookie is set by the API on :8002, but it is host-only on `localhost`, so
 * the browser sends it to the Next server on :3002 too — we just forward it back.
 */
export async function getSession(): Promise<User | null> {
  return apiGet<User>("/api/auth/me");
}

/**
 * GET an authenticated API route from a server component, forwarding the
 * session cookie. Returns null on any failure — pages decide what that means
 * (redirect to /login, render an empty state) rather than crashing.
 */
export async function apiGet<T>(path: string): Promise<T | null> {
  const token = cookies().get(SESSION_COOKIE)?.value;
  if (!token) return null;

  try {
    const res = await fetch(`${API_URL}${path}`, {
      headers: { cookie: `${SESSION_COOKIE}=${token}` },
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    // API down — treat as signed out rather than crashing the page.
    return null;
  }
}
