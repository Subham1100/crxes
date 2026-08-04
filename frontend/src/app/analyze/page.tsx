import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { AnalyzeConsole } from "@/components/analyze-console";
import { AppShell } from "@/components/app-shell";
import { getSession } from "@/lib/session";

export const metadata: Metadata = { title: "Analyze — crxes.app" };

export default async function AnalyzePage() {
  const user = await getSession();
  if (!user) redirect("/login");

  return (
    <AppShell>
      <p className="font-mono text-label uppercase text-muted">Manual source</p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight">Analyze a log sample</h1>
      <p className="mt-3 max-w-xl text-secondary">
        Paste logs from any source. Four agents run in sequence — Log Parser, Pattern Detector, Root
        Cause Analyzer, Bug Predictor — and forecast what is about to break. Connecting Datadog,
        CloudWatch, and the rest lands in Phase 4.
      </p>

      <div className="mt-10">
        <AnalyzeConsole />
      </div>
    </AppShell>
  );
}
