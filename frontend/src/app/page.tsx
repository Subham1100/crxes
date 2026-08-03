const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";

const AGENTS = [
  { name: "Log Parser", color: "var(--agent-1)" },
  { name: "Pattern Detector", color: "var(--agent-2)" },
  { name: "Root Cause Analyzer", color: "var(--agent-3)" },
  { name: "Bug Predictor", color: "var(--agent-4)" },
];

async function getApiHealth(): Promise<string> {
  try {
    const res = await fetch(`${API_URL}/api/health/`, { cache: "no-store" });
    if (!res.ok) return `unreachable (HTTP ${res.status})`;
    const body = (await res.json()) as { status: string; database: string };
    return `${body.status} · db ${body.database}`;
  } catch {
    return "unreachable";
  }
}

export default async function Home() {
  const health = await getApiHealth();

  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-8 px-6 py-16">
      <div>
        <p className="font-mono text-label uppercase text-muted">Phase 0 · scaffolding</p>
        <h1 className="mt-2 text-2xl font-semibold">crxes.app</h1>
        <p className="mt-2 text-secondary">
          Multi-agent bug prediction. The shell is up — pages land in later phases.
        </p>
      </div>

      <div className="rounded-lg border border-border bg-surface p-4">
        <p className="font-mono text-label uppercase text-muted">Backend</p>
        <p className="mt-1 font-mono text-code text-primary">{health}</p>
      </div>

      <div className="rounded-lg border border-border bg-surface p-4">
        <p className="font-mono text-label uppercase text-muted">Pipeline</p>
        <ul className="mt-3 space-y-2">
          {AGENTS.map((agent, i) => (
            <li key={agent.name} className="flex items-center gap-3">
              <span
                className="h-2 w-2 shrink-0 rounded-full"
                style={{ backgroundColor: agent.color }}
              />
              <span className="font-mono text-code text-muted">{i + 1}</span>
              <span className="font-mono text-code text-primary">{agent.name}</span>
            </li>
          ))}
        </ul>
      </div>
    </main>
  );
}
