"use client";

import { useState } from "react";

import { AnalysisReport } from "@/components/analysis-report";
import { API_URL, errorMessage } from "@/lib/api";
import { AGENTS, type AnalysisDetail } from "@/lib/types";

const AGENT_COLORS = ["var(--agent-1)", "var(--agent-2)", "var(--agent-3)", "var(--agent-4)"];

const SAMPLE = `2026-08-04T14:58:11.004Z INFO  [api.gateway] request complete path=/checkout status=200 duration=142ms
2026-08-04T14:58:12.881Z WARN  [db.pool] pool at 18/20 connections, wait_queue=3
2026-08-04T14:58:14.220Z INFO  [api.gateway] request complete path=/checkout status=200 duration=489ms
2026-08-04T14:58:19.507Z WARN  [db.pool] pool at 20/20 connections, wait_queue=11
2026-08-04T14:58:21.113Z ERROR [db.pool] connection acquire timeout after 5000ms
    at Pool.acquire (/app/node_modules/pg-pool/index.js:212)
    at CheckoutService.reserve (/app/src/checkout.ts:88)
2026-08-04T14:58:21.115Z ERROR [api.gateway] request failed path=/checkout status=503 duration=5012ms
2026-08-04T14:58:22.640Z INFO  [checkout.retry] scheduling retry attempt=1 order=8831
2026-08-04T14:58:24.902Z WARN  [db.pool] pool at 20/20 connections, wait_queue=27
2026-08-04T14:58:25.001Z ERROR [db.pool] connection acquire timeout after 5000ms
2026-08-04T14:58:25.330Z INFO  [checkout.retry] scheduling retry attempt=2 order=8831
2026-08-04T14:58:27.744Z INFO  [checkout.retry] scheduling retry attempt=1 order=8832
2026-08-04T14:58:29.118Z WARN  [db.pool] pool at 20/20 connections, wait_queue=44
2026-08-04T14:58:31.560Z ERROR [db.pool] connection acquire timeout after 5000ms
2026-08-04T14:58:31.902Z INFO  [checkout.retry] scheduling retry attempt=3 order=8831
2026-08-04T14:58:33.007Z INFO  [metrics] heap_used=1.71GB heap_limit=2.00GB gc_pause=180ms`;

function RunningState() {
  return (
    <div className="rounded-xl border border-border bg-surface p-6">
      <p className="font-mono text-label uppercase text-muted">Running</p>
      <p className="mt-2 text-secondary">
        Four agents run in sequence against your sample. This takes a minute or two — the page is
        waiting on the API, so leave it open.
      </p>
      <ol className="mt-5 space-y-2.5">
        {AGENTS.map((agent, i) => (
          <li key={agent.key} className="flex items-center gap-3">
            <span
              className="h-[7px] w-[7px] shrink-0 animate-pulse rounded-[2px]"
              style={{ backgroundColor: AGENT_COLORS[i], animationDelay: `${i * 180}ms` }}
              aria-hidden="true"
            />
            <span className="text-secondary">{agent.name}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function AnalyzeConsole() {
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisDetail | null>(null);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setAnalysis(null);
    setPending(true);

    try {
      const res = await fetch(`${API_URL}/api/analyses`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ logs: text }),
      });

      const body = await res.json().catch(() => null);
      if (!res.ok) {
        setError(errorMessage(body, `Request failed (HTTP ${res.status})`));
        return;
      }
      setAnalysis(body as AnalysisDetail);
    } catch {
      setError("Can't reach the API. Is the backend running on port 8002?");
    } finally {
      setPending(false);
    }
  }

  const lineCount = text.trim() ? text.trim().split("\n").length : 0;

  return (
    <div className="space-y-10">
      <form onSubmit={onSubmit}>
        <div className="flex items-end justify-between gap-4">
          <label htmlFor="logs" className="font-mono text-label uppercase text-muted">
            Paste logs
          </label>
          <button
            type="button"
            onClick={() => setText(SAMPLE)}
            disabled={pending}
            className="font-mono text-label uppercase text-muted underline underline-offset-4 transition hover:text-secondary disabled:opacity-50"
          >
            Load a sample
          </button>
        </div>

        <textarea
          id="logs"
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={pending}
          rows={14}
          spellCheck={false}
          placeholder={"2026-08-04T14:58:21.113Z ERROR [db.pool] connection acquire timeout…"}
          className="mt-1.5 w-full resize-y rounded-lg border border-border bg-deep px-3.5 py-3 font-mono text-code text-primary placeholder:text-muted outline-none transition focus:border-[#3b82f6] focus:ring-1 focus:ring-[#3b82f6]/40 disabled:opacity-60"
        />

        <div className="mt-3 flex flex-wrap items-center justify-between gap-4">
          <p className="font-mono text-label uppercase text-muted">
            {lineCount.toLocaleString()} {lineCount === 1 ? "line" : "lines"}
          </p>
          <button
            type="submit"
            disabled={pending || lineCount === 0}
            className="rounded-lg bg-[#e2e8f0] px-5 py-2.5 font-medium text-[#0b1120] transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-60"
          >
            {pending ? "Analyzing…" : "Run analysis"}
          </button>
        </div>

        {error && (
          <p
            role="alert"
            className="mt-4 rounded-lg border px-3 py-2.5"
            style={{
              borderColor: "var(--sev-critical)",
              backgroundColor: "rgba(239,68,68,0.08)",
              color: "var(--sev-critical)",
            }}
          >
            {error}
          </p>
        )}
      </form>

      {pending && <RunningState />}
      {analysis && <AnalysisReport analysis={analysis} />}
    </div>
  );
}
