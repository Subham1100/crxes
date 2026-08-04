"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { GitHubMark, GoogleMark } from "@/components/logos";
import { API_URL, errorMessage } from "@/lib/api";

type Mode = "login" | "signup";

const COPY = {
  login: {
    title: "Sign in",
    subtitle: "Pick up where your last analysis left off.",
    submit: "Sign in",
    pending: "Signing in…",
    footer: { text: "No account yet?", link: "/signup", label: "Create one" },
  },
  signup: {
    title: "Create your account",
    subtitle: "Connect a source and get your first forecast in about two minutes.",
    submit: "Create account",
    pending: "Creating account…",
    footer: { text: "Already have an account?", link: "/login", label: "Sign in" },
  },
} as const;

const FIELD =
  "w-full rounded-lg border border-border bg-deep px-3.5 py-2.5 text-primary placeholder:text-muted outline-none transition focus:border-[#3b82f6] focus:ring-1 focus:ring-[#3b82f6]/40";

export function AuthForm({ mode }: { mode: Mode }) {
  const copy = COPY[mode];
  const router = useRouter();

  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setPending(true);

    const data = new FormData(event.currentTarget);
    const payload: Record<string, string> = {
      email: String(data.get("email") ?? ""),
      password: String(data.get("password") ?? ""),
    };
    if (mode === "signup") {
      const name = String(data.get("name") ?? "").trim();
      if (name) payload.name = name;
    }

    try {
      const res = await fetch(`${API_URL}/api/auth/${mode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // Required for the API to set its session cookie on this origin.
        credentials: "include",
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => null);
        setError(errorMessage(body, `Request failed (HTTP ${res.status})`));
        setPending(false);
        return;
      }

      router.push("/dashboard");
      router.refresh();
    } catch {
      setError("Can't reach the API. Is the backend running on port 8001?");
      setPending(false);
    }
  }

  return (
    <div className="w-full max-w-[420px]">
      <h1 className="text-2xl font-semibold tracking-tight">{copy.title}</h1>
      <p className="mt-2 text-secondary">{copy.subtitle}</p>

      <div className="mt-7 grid gap-3 sm:grid-cols-2">
        {[
          { label: "GitHub", Mark: GitHubMark },
          { label: "Google", Mark: GoogleMark },
        ].map(({ label, Mark }) => (
          <button
            key={label}
            type="button"
            disabled
            title="OAuth ships in Phase 2"
            className="flex cursor-not-allowed items-center justify-center gap-2.5 rounded-lg border border-border bg-surface px-4 py-2.5 text-secondary opacity-50"
          >
            <Mark className="h-[18px] w-[18px]" />
            {label}
          </button>
        ))}
      </div>
      <p className="mt-2 font-mono text-label uppercase text-muted">
        OAuth arrives in Phase 2 — use email for now
      </p>

      <div className="my-6 flex items-center gap-3">
        <span className="h-px flex-1 bg-border" />
        <span className="font-mono text-label uppercase text-muted">or</span>
        <span className="h-px flex-1 bg-border" />
      </div>

      <form onSubmit={onSubmit} className="space-y-4" noValidate>
        {mode === "signup" && (
          <div>
            <label htmlFor="name" className="font-mono text-label uppercase text-muted">
              Name <span className="normal-case tracking-normal">(optional)</span>
            </label>
            <input
              id="name"
              name="name"
              type="text"
              autoComplete="name"
              maxLength={255}
              placeholder="Ada Lovelace"
              className={`mt-1.5 ${FIELD}`}
            />
          </div>
        )}

        <div>
          <label htmlFor="email" className="font-mono text-label uppercase text-muted">
            Email
          </label>
          <input
            id="email"
            name="email"
            type="email"
            required
            autoComplete="email"
            placeholder="you@company.com"
            className={`mt-1.5 ${FIELD}`}
          />
        </div>

        <div>
          <label htmlFor="password" className="font-mono text-label uppercase text-muted">
            Password
          </label>
          <input
            id="password"
            name="password"
            type="password"
            required
            minLength={8}
            maxLength={128}
            autoComplete={mode === "signup" ? "new-password" : "current-password"}
            placeholder="••••••••"
            className={`mt-1.5 ${FIELD}`}
          />
          {mode === "signup" && <p className="mt-1.5 text-muted">At least 8 characters.</p>}
        </div>

        {error && (
          <p
            role="alert"
            className="rounded-lg border px-3 py-2.5"
            style={{
              borderColor: "var(--sev-critical)",
              backgroundColor: "rgba(239,68,68,0.08)",
              color: "var(--sev-critical)",
            }}
          >
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={pending}
          className="w-full rounded-lg bg-[#e2e8f0] px-5 py-3 font-medium text-[#0b1120] transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-60"
        >
          {pending ? copy.pending : copy.submit}
        </button>
      </form>

      <p className="mt-6 text-secondary">
        {copy.footer.text}{" "}
        <Link href={copy.footer.link} className="text-primary underline underline-offset-4">
          {copy.footer.label}
        </Link>
      </p>
    </div>
  );
}
