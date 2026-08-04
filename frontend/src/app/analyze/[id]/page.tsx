import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";

import { AnalysisReport } from "@/components/analysis-report";
import { AppShell } from "@/components/app-shell";
import { apiGet, getSession } from "@/lib/session";
import type { AnalysisDetail } from "@/lib/types";

export const metadata: Metadata = { title: "Analysis — crxes.app" };

export default async function AnalysisPage({ params }: { params: { id: string } }) {
  const user = await getSession();
  if (!user) redirect("/login");

  const analysis = await apiGet<AnalysisDetail>(`/api/analyses/${params.id}`);
  if (!analysis) notFound();

  return (
    <AppShell>
      <p className="font-mono text-label uppercase text-muted">
        {new Date(analysis.created_at).toLocaleString()}
      </p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight">Analysis</h1>
      <p className="mt-2 font-mono text-code text-muted">{analysis.id}</p>

      <div className="mt-10">
        <AnalysisReport analysis={analysis} />
      </div>
    </AppShell>
  );
}
