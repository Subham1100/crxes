import { type CostEstimate, type ModelCost } from "@/lib/types";

/**
 * Costs here span three orders of magnitude — tenths of a cent on a nano model
 * up to dollars on a large paste — so the number of decimals follows the value
 * rather than being fixed, and nothing rounds to a misleading "$0.00".
 */
export function formatUsd(usd: number): string {
  if (usd === 0) return "$0";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  if (usd < 1) return `$${usd.toFixed(3)}`;
  return `$${usd.toFixed(2)}`;
}

function compactTokens(n: number): string {
  if (n < 1000) return String(n);
  return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
}

/** How the row compares to what crxes will actually be charged. */
function relativeToCurrent(model: ModelCost, current: number): string {
  if (model.is_current || current === 0 || model.total_usd === 0) return "";
  const ratio = current / model.total_usd;
  if (ratio >= 1) return `${ratio.toFixed(ratio >= 10 ? 0 : 1)}× cheaper`;
  return `${(1 / ratio).toFixed(1)}× dearer`;
}

function ModelRow({ model, current }: { model: ModelCost; current: number }) {
  const relative = relativeToCurrent(model, current);

  return (
    <tr className={model.is_current ? "bg-elevated" : undefined}>
      <td className="py-2 pl-4 pr-3">
        <span className="text-primary">{model.label}</span>
        {model.is_current && (
          <span className="ml-2 font-mono text-label uppercase text-agent-4">running</span>
        )}
        {!model.fits_context && (
          <span className="ml-2 font-mono text-label uppercase text-sev-high">
            over context
          </span>
        )}
      </td>
      <td className="px-3 py-2 font-mono text-code text-muted">{model.provider_label}</td>
      <td className="px-3 py-2 text-right font-mono text-code text-muted">
        {model.input_per_mtok} / {model.output_per_mtok}
      </td>
      <td className="px-3 py-2 text-right font-mono text-code text-secondary">{relative}</td>
      <td className="py-2 pl-3 pr-4 text-right font-mono text-code text-primary">
        {formatUsd(model.total_usd)}
      </td>
    </tr>
  );
}

/**
 * Shows what the four-agent pipeline will cost for the pasted sample, before
 * it runs — the configured model's price first, then every other model the
 * catalog prices, so the comparison is one glance rather than a spreadsheet.
 */
export function CostEstimatePanel({
  estimate,
  stale,
}: {
  estimate: CostEstimate;
  /** True while a newer estimate is in flight — dims rather than blanks. */
  stale?: boolean;
}) {
  const current = estimate.models.find((m) => m.is_current) ?? estimate.models[0];
  const cheapest = estimate.models.reduce((a, b) => (b.total_usd < a.total_usd ? b : a));

  return (
    <section
      aria-label="Cost estimate"
      className={`rounded-xl border border-border bg-surface transition-opacity ${
        stale ? "opacity-50" : ""
      }`}
    >
      <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-4 px-5 py-4">
        <div>
          <p className="font-mono text-label uppercase text-muted">
            Estimated cost · {current.label}
          </p>
          <p className="mt-1 font-mono text-stat text-primary">{formatUsd(current.total_usd)}</p>
        </div>

        <dl className="flex flex-wrap gap-x-7 gap-y-2">
          {[
            // Parsed entries, not raw lines — stack frames fold into the entry
            // above them before anything is sent.
            ["Entries", estimate.log_line_count.toLocaleString()],
            [
              "Tokens in / out",
              `${compactTokens(estimate.input_tokens)} / ${compactTokens(estimate.output_tokens)}`,
            ],
            ["Effort", estimate.effort],
            ["Cheapest", `${cheapest.label} · ${formatUsd(cheapest.total_usd)}`],
          ].map(([label, value]) => (
            <div key={label}>
              <dt className="font-mono text-label uppercase text-muted">{label}</dt>
              <dd className="mt-0.5 font-mono text-code text-secondary">{value}</dd>
            </div>
          ))}
        </dl>
      </div>

      {estimate.dropped_lines > 0 && (
        <p className="border-t border-border px-5 py-2.5 font-mono text-code text-sev-high">
          {estimate.dropped_lines.toLocaleString()} lines past the cap will be dropped before the
          run — the estimate prices only what the agents will see.
        </p>
      )}

      <details className="border-t border-border">
        <summary className="cursor-pointer px-5 py-3 font-mono text-label uppercase text-muted transition hover:text-secondary">
          Compare {estimate.models.length} models
        </summary>

        <div className="overflow-x-auto border-t border-border">
          <table className="w-full min-w-[34rem] border-collapse text-body">
            <thead>
              <tr className="border-b border-border">
                {["Model", "Provider", "$/MTok in / out", "vs. running", "Run cost"].map(
                  (heading, i) => (
                    <th
                      key={heading}
                      className={`px-3 py-2 font-mono text-label font-normal uppercase text-muted ${
                        i === 0 ? "pl-4 text-left" : i === 1 ? "text-left" : "text-right"
                      } ${i === 4 ? "pr-4" : ""}`}
                    >
                      {heading}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {estimate.models.map((model) => (
                <ModelRow key={model.id} model={model} current={current.total_usd} />
              ))}
            </tbody>
          </table>
        </div>

        <div className="border-t border-border px-5 py-3">
          <p className="font-mono text-label uppercase text-muted">Per agent</p>
          <ul className="mt-2 space-y-1">
            {estimate.stages.map((stage) => (
              <li key={stage.key} className="flex justify-between gap-4 font-mono text-code">
                <span className="text-secondary">{stage.name}</span>
                <span className="text-muted">
                  {compactTokens(stage.input_tokens)} in ·{" "}
                  {compactTokens(stage.output_tokens)} out (
                  {compactTokens(stage.thinking_tokens)} thinking)
                </span>
              </li>
            ))}
          </ul>
        </div>
      </details>

      <p className="border-t border-border px-5 py-3 text-body text-muted">
        {estimate.token_source === "counted"
          ? "Input tokens counted by the Claude API"
          : "Input tokens estimated from character count"}
        ; each agent&rsquo;s output length and thinking are projections, so the total moves with
        what the model actually writes. Non-Anthropic figures scale the same token count by that
        tokenizer&rsquo;s typical ratio and price it at published list rates (checked{" "}
        {estimate.prices_updated}) — a comparison, not a quote.
      </p>
    </section>
  );
}
