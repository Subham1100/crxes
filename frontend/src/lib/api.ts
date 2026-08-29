export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";

/**
 * The same API, addressed from the server rather than the browser.
 *
 * Under Docker the two differ: the browser resolves the published host port
 * (localhost:8001), while server components sit inside the compose network and
 * must use the service name (backend:8001). Unset outside Docker, where the
 * fallback makes this identical to API_URL.
 */
export const SERVER_API_URL = process.env.INTERNAL_API_URL ?? API_URL;

/** Name of the httpOnly session cookie the API sets (backend/config.py). */
export const SESSION_COOKIE = "crxes_session";

type FastApiError = {
  detail?: string | { msg?: string }[];
};

/** FastAPI returns `detail` as a string (HTTPException) or a list (422). */
export function errorMessage(body: unknown, fallback = "Something went wrong"): string {
  const detail = (body as FastApiError)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg;
  return fallback;
}
