import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { apiGet, getSession } from "@/lib/session";
import type { Analysis } from "@/lib/types";

export const metadata: Metadata = { title: "Dashboard — crxes.app" };

const STATUS_COLOR: Record<string, string> = {
  done: "var(--sev-low)",
  failed: "var(--sev-critical)",
  running: "var(--sev-medium)",
  pending: "var(--text-muted)",
};

export default async function DashboardPage() {
  const user = await getSession();
  if (!user) redirect("/login");

  const analyses = (await apiGet<Analysis[]>("/api/analyses")) ?? [];

  return (
    <AppShell>
      <p className="font-mono text-label uppercase text-muted">Signed in</p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight">
        Welcome{user.name ? `, ${user.name.split(" ")[0]}` : ""}.
      </h1>
      <p className="mt-3 max-w-xl text-secondary">
        Paste a log sample and the four-agent pipeline will forecast what is about to break.
        Scheduled pulls from Datadog, CloudWatch, and Sentry arrive in later phases.
      </p>

      <Link
        href="/analyze"
        className="mt-6 inline-block rounded-lg bg-[#e2e8f0] px-5 py-2.5 font-medium text-[#0b1120] transition hover:bg-white"
      >
        Run an analysis
      </Link>

      <section className="mt-12">
        <h2 className="text-title font-semibold text-primary">Recent analyses</h2>

        {analyses.length === 0 ? (
          <p className="mt-3 rounded-lg border border-border bg-surface px-4 py-3 text-secondary">
            Nothing yet — your first analysis will show up here.
          </p>
        ) : (
          <div className="mt-4 overflow-hidden rounded-xl border border-border bg-surface">
            {analyses.map((a, i) => (
              <Link
                key={a.id}
                href={`/analyze/${a.id}`}
                className={`flex items-center gap-4 px-5 py-3.5 transition hover:bg-elevated ${
                  i > 0 ? "border-t border-border" : ""
                }`}
              >
                <span
                  className="h-[7px] w-[7px] shrink-0 rounded-[2px]"
                  style={{ backgroundColor: STATUS_COLOR[a.status] ?? "var(--text-muted)" }}
                  aria-hidden="true"
                />
                <span className="font-mono text-code text-primary">
                  {new Date(a.created_at).toLocaleString()}
                </span>
                <span className="font-mono text-label uppercase text-muted">
                  {(a.log_line_count ?? 0).toLocaleString()} lines
                </span>
                <span className="ml-auto font-mono text-label uppercase text-muted">{a.status}</span>
              </Link>
            ))}
          </div>
        )}
      </section>
    </AppShell>
  );
}
