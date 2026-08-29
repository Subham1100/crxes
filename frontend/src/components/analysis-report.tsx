import { formatUsd } from "@/components/cost-estimate";
import { AGENTS, type AgentKey, type AnalysisDetail, type Severity } from "@/lib/types";

const AGENT_COLOR: Record<AgentKey, string> = {
  parser: "var(--agent-1)",
  pattern: "var(--agent-2)",
  rootcause: "var(--agent-3)",
  predictor: "var(--agent-4)",
};

const SEVERITY_COLOR: Record<Severity, string> = {
  critical: "var(--sev-critical)",
  high: "var(--sev-high)",
  medium: "var(--sev-medium)",
  low: "var(--sev-low)",
};

function outputFor(analysis: AnalysisDetail, key: AgentKey): string | null {
  return {
    parser: analysis.agent_parser_output,
    pattern: analysis.agent_pattern_output,
    rootcause: analysis.agent_rootcause_output,
    predictor: analysis.agent_predictor_output,
  }[key];
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-border bg-surface px-4 py-3">
      <p className="font-mono text-label uppercase text-muted">{label}</p>
      <p className="mt-1 font-mono text-stat text-primary">{value}</p>
      {hint && <p className="mt-0.5 font-mono text-label text-muted">{hint}</p>}
    </div>
  );
}

function PredictionCard({ p }: { p: AnalysisDetail["predictions"][number] }) {
  const color = SEVERITY_COLOR[p.severity];
  const rows: [string, string | null][] = [
    ["Impact", p.impact],
    ["Root cause", p.root_cause],
    ["Recommended action", p.recommended_action],
  ];

  return (
    <article
      className="rounded-xl border border-border bg-surface p-5"
      style={{ borderLeft: `3px solid ${color}` }}
    >
      <div className="flex flex-wrap items-center gap-3">
        <span
          className="rounded-md px-2 py-0.5 font-mono text-label uppercase"
          style={{ color, backgroundColor: `color-mix(in srgb, ${color} 14%, transparent)` }}
        >
          {p.severity}
        </span>
        {p.confidence !== null && (
          <span className="font-mono text-label uppercase text-muted">
            {p.confidence}% confidence
          </span>
        )}
        {p.eta && <span className="font-mono text-label uppercase text-muted">ETA {p.eta}</span>}
      </div>

      <h3 className="mt-3 text-title font-semibold text-primary">{p.title}</h3>
      {p.description && <p className="mt-2 text-secondary">{p.description}</p>}

      <dl className="mt-4 space-y-2.5">
        {rows.map(
          ([label, value]) =>
            value && (
              <div key={label}>
                <dt className="font-mono text-label uppercase text-muted">{label}</dt>
                <dd className="mt-0.5 text-secondary">{value}</dd>
              </div>
            ),
        )}
      </dl>
    </article>
  );
}

/** Renders a completed (or failed) analysis: predictions first, then the trace. */
export function AnalysisReport({ analysis }: { analysis: AnalysisDetail }) {
  const failed = analysis.status === "failed";
  const stalledAt = AGENTS[analysis.current_agent ?? 0];

  return (
    <div className="space-y-10">
      {failed && (
        <p
          role="alert"
          className="rounded-lg border px-4 py-3"
          style={{
            borderColor: "var(--sev-critical)",
            backgroundColor: "rgba(239,68,68,0.08)",
            color: "var(--sev-critical)",
          }}
        >
          {analysis.error_message ?? "The pipeline failed."}
          {stalledAt && ` (stopped at ${stalledAt.name})`}
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Stat label="Log lines" value={(analysis.log_line_count ?? 0).toLocaleString()} />
        <Stat label="Predictions" value={String(analysis.predictions.length)} />
        <Stat
          label="Duration"
          value={analysis.duration_ms ? `${(analysis.duration_ms / 1000).toFixed(1)}s` : "—"}
        />
        <Stat
          label="Tokens"
          value={analysis.total_tokens_used?.toLocaleString() ?? "—"}
          // The split is what makes the cost above legible: output bills at ~5x input.
          hint={
            analysis.input_tokens !== null && analysis.output_tokens !== null
              ? `${analysis.input_tokens.toLocaleString()} in · ${analysis.output_tokens.toLocaleString()} out`
              : undefined
          }
        />
        <Stat
          label="Cost"
          value={analysis.cost_usd !== null ? formatUsd(analysis.cost_usd) : "—"}
          hint={analysis.model ?? undefined}
        />
      </div>

      <section>
        <h2 className="text-title font-semibold text-primary">Predictions</h2>
        {analysis.predictions.length === 0 ? (
          <p className="mt-3 rounded-lg border border-border bg-surface px-4 py-3 text-secondary">
            {failed
              ? "The pipeline stopped before the Bug Predictor ran."
              : "No failures predicted — the Bug Predictor read this sample as a system behaving normally."}
          </p>
        ) : (
          <div className="mt-4 space-y-4">
            {analysis.predictions.map((p) => (
              <PredictionCard key={p.id} p={p} />
            ))}
          </div>
        )}
      </section>

      <section>
        <h2 className="text-title font-semibold text-primary">Agent trace</h2>
        <p className="mt-1 text-secondary">
          Each agent reads only what the ones before it produced.
        </p>

        <div className="mt-4 space-y-4">
          {AGENTS.map((agent) => {
            const output = outputFor(analysis, agent.key);
            const color = AGENT_COLOR[agent.key];

            return (
              <details
                key={agent.key}
                open={agent.key === "rootcause"}
                className="overflow-hidden rounded-xl border border-border bg-surface"
              >
                <summary className="flex cursor-pointer items-center gap-3 px-5 py-3.5">
                  <span
                    className="h-[7px] w-[7px] shrink-0 rounded-[2px]"
                    style={{ backgroundColor: color }}
                    aria-hidden="true"
                  />
                  <span className="text-title font-medium text-primary">{agent.name}</span>
                  <span className="ml-auto font-mono text-label uppercase text-muted">
                    {output ? `${output.length.toLocaleString()} chars` : "did not run"}
                  </span>
                </summary>

                {output && (
                  <div className="border-t border-border px-5 py-4">
                    {/* Agents emit Markdown; rendered as preformatted text so the
                        structure survives without pulling in a Markdown parser. */}
                    <pre className="whitespace-pre-wrap break-words font-sans text-secondary">
                      {output}
                    </pre>
                  </div>
                )}
              </details>
            );
          })}
        </div>
      </section>
    </div>
  );
}
