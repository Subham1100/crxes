import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { SignOutButton } from "@/components/sign-out-button";
import { Wordmark } from "@/components/wordmark";
import { getSession } from "@/lib/session";

export const metadata: Metadata = { title: "Dashboard — crxes.app" };

/**
 * Placeholder shell. Proves the session round-trip (cookie → API → Postgres);
 * sources, analyses, and predictions land in the phases that build them.
 */
export default async function DashboardPage() {
  const user = await getSession();
  if (!user) redirect("/login");

  const rows: [string, string][] = [
    ["Email", user.email],
    ["Name", user.name ?? "—"],
    ["Plan", user.plan],
    ["User ID", user.id],
    ["Joined", new Date(user.created_at).toLocaleString()],
  ];

  return (
    <div className="min-h-screen">
      <header className="border-b border-border px-6 py-4">
        <div className="mx-auto flex max-w-4xl items-center justify-between">
          <Wordmark href="/dashboard" />
          <SignOutButton />
        </div>
      </header>

      <main className="mx-auto max-w-4xl px-6 py-14">
        <p className="font-mono text-label uppercase text-muted">Signed in</p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight">
          Welcome{user.name ? `, ${user.name.split(" ")[0]}` : ""}.
        </h1>
        <p className="mt-3 max-w-xl text-secondary">
          Your account is live in Postgres. Connecting a log source is the next step — the wizard
          ships with the source phase.
        </p>

        <div className="mt-10 overflow-hidden rounded-xl border border-border bg-surface">
          {rows.map(([label, value], i) => (
            <div
              key={label}
              className={`flex items-center justify-between gap-6 px-5 py-3.5 ${
                i > 0 ? "border-t border-border" : ""
              }`}
            >
              <span className="font-mono text-label uppercase text-muted">{label}</span>
              <span className="truncate font-mono text-code text-primary">{value}</span>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}
