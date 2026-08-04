export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8002";

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
