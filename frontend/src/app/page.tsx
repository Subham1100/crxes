import Link from "next/link";

import { GitHubMark, GoogleMark, PROVIDERS } from "@/components/logos";
import { AGENTS } from "@/lib/types";

/* -------------------------------------------------------------------------- */
/* Content                                                                     */
/* -------------------------------------------------------------------------- */

const AGENT_COPY: Record<(typeof AGENTS)[number]["key"], { color: string; blurb: string }> = {
  parser: {
    color: "var(--agent-1)",
    blurb:
      "Normalizes every provider's payload into one shape — timestamp, level, service, message — and throws away the noise.",
  },
  pattern: {
    color: "var(--agent-2)",
    blurb:
      "Clusters the normalized lines into recurring signatures and flags the ones whose rate is bending upward.",
  },
  rootcause: {
    color: "var(--agent-3)",
    blurb:
      "Traces each rising cluster back through the surrounding lines to the change or dependency that explains it.",
  },
  predictor: {
    color: "var(--agent-4)",
    blurb:
      "Turns causes into forecasts: what breaks, how badly, how confident, how soon, and what to do first.",
  },
};

const STEPS = [
  {
    n: "01",
    title: "Connect a source",
    body: "Walk the wizard, paste read-only credentials, hit Test connection. We validate the key against the provider and show you the sample log count before anything is saved.",
  },
  {
    n: "02",
    title: "Pull logs",
    body: "Pull now fetches on demand. Or set a schedule — every 15 minutes through every 4 hours — and Celery Beat keeps pulling without you.",
  },
  {
    n: "03",
    title: "Read the forecast",
    body: "The pipeline runs and streams its progress live. Three to five predictions land, ranked by severity, each with the root cause behind it.",
  },
];

const FEATURES = [
  {
    title: "Scheduled pulls",
    body: "Pick 15 min, 30 min, hourly, or 4-hourly per source. Celery Beat owns the cadence; you own the dial.",
  },
  {
    title: "Auto-analyze",
    body: "Fire the full pipeline after every scheduled pull, so predictions are waiting for you instead of the other way round.",
  },
  {
    title: "Live agent progress",
    body: "Server-sent events push each agent's start and finish as it happens — no spinner, no polling, no guessing where it is.",
  },
  {
    title: "Browsable history",
    body: "Every analysis and prediction is stored. Filter by source, severity, or date and reopen any run with its full agent output.",
  },
  {
    title: "Accuracy feedback",
    body: "Mark a prediction accurate or inaccurate with a note. Your calls become the scorecard for how much to trust the next one.",
  },
  {
    title: "Source health",
    body: "The dashboard surfaces failing credentials, stale pulls, and paused sources before a silent gap turns into a blind spot.",
  },
];

const SAMPLE_PREDICTION = {
  title: "Checkout writes will start timing out",
  severity: "critical" as const,
  confidence: 0.87,
  eta: "~4 hours",
  impact: "Payment capture on the EU cluster",
  rootCause:
    "payments-api connection pool exhaustion — pool_size=20 against a checkout worker count raised to 64 in the 14:02 deploy. Wait-time p99 has climbed 8x in 40 minutes.",
  action:
    "Raise SQLALCHEMY_POOL_SIZE to 64 on payments-api, or roll back the worker-count bump in deploy 8f21c4.",
};

const SEVERITY_STYLE = {
  critical: { color: "var(--sev-critical)", label: "Critical" },
  high: { color: "var(--sev-high)", label: "High" },
  medium: { color: "var(--sev-medium)", label: "Medium" },
  low: { color: "var(--sev-low)", label: "Low" },
};

/* -------------------------------------------------------------------------- */
/* Primitives                                                                  */
/* -------------------------------------------------------------------------- */

function Eyebrow({ children }: { children: React.ReactNode }) {
  return <p className="font-mono text-label uppercase text-muted">{children}</p>;
}

function Section({
  id,
  children,
  className = "",
}: {
  id?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section id={id} className={`border-t border-border px-6 py-20 sm:py-24 ${className}`}>
      <div className="mx-auto max-w-6xl">{children}</div>
    </section>
  );
}

function Wordmark() {
  return (
    <Link href="/" className="flex items-center gap-2.5">
      <span className="grid grid-cols-2 gap-[3px]" aria-hidden="true">
        {[
          "var(--agent-1)",
          "var(--agent-2)",
          "var(--agent-3)",
          "var(--agent-4)",
        ].map((c) => (
          <span key={c} className="h-[7px] w-[7px] rounded-[2px]" style={{ backgroundColor: c }} />
        ))}
      </span>
      <span className="font-mono text-title font-semibold tracking-tight text-primary">crxes</span>
    </Link>
  );
}

function OAuthButtons({ size = "lg" }: { size?: "lg" | "sm" }) {
  const pad = size === "lg" ? "px-5 py-3" : "px-4 py-2.5";
  return (
    <div className="flex flex-col gap-3 sm:flex-row">
      <Link
        href="/login?provider=github"
        className={`flex items-center justify-center gap-2.5 rounded-lg bg-[#e2e8f0] ${pad} font-medium text-[#0b1120] transition hover:bg-white`}
      >
        <GitHubMark className="h-[18px] w-[18px]" />
        Continue with GitHub
      </Link>
      <Link
        href="/login?provider=google"
        className={`flex items-center justify-center gap-2.5 rounded-lg border border-border bg-elevated ${pad} font-medium text-primary transition hover:border-[#334155] hover:bg-[#182238]`}
      >
        <GoogleMark className="h-[18px] w-[18px]" />
        Continue with Google
      </Link>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Sections                                                                    */
/* -------------------------------------------------------------------------- */

function Nav() {
  return (
    <header className="sticky top-0 z-20 border-b border-border bg-deep/80 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
        <Wordmark />
        <nav className="hidden items-center gap-7 text-muted md:flex">
          <a href="#how" className="transition hover:text-primary">
            How it works
          </a>
          <a href="#agents" className="transition hover:text-primary">
            Agents
          </a>
          <a href="#sources" className="transition hover:text-primary">
            Sources
          </a>
          <a href="#pricing" className="transition hover:text-primary">
            Pricing
          </a>
        </nav>
        <div className="flex items-center gap-4">
          <Link href="/login" className="hidden text-secondary transition hover:text-primary sm:block">
            Sign in
          </Link>
          <Link
            href="/login"
            className="rounded-lg bg-[#e2e8f0] px-4 py-2 font-medium text-[#0b1120] transition hover:bg-white"
          >
            Start free
          </Link>
        </div>
      </div>
    </header>
  );
}

function Hero() {
  return (
    <section className="relative overflow-hidden px-6 pb-20 pt-16 sm:pt-24">
      {/* Ambient wash — pure decoration, kept behind everything. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[520px] opacity-60"
        style={{
          background:
            "radial-gradient(60% 60% at 25% 0%, rgba(59,130,246,0.18) 0%, transparent 70%), radial-gradient(50% 50% at 80% 10%, rgba(139,92,246,0.14) 0%, transparent 70%)",
        }}
      />
      <div className="mx-auto grid max-w-6xl items-start gap-14 lg:grid-cols-[minmax(0,1fr)_minmax(0,520px)]">
        <div>
          <div className="inline-flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1">
            <span className="h-1.5 w-1.5 rounded-full bg-agent-4" />
            <span className="font-mono text-label uppercase text-secondary">
              Four agents · one pipeline
            </span>
          </div>

          <h1 className="mt-6 text-4xl font-semibold leading-[1.1] tracking-tight text-primary sm:text-5xl">
            Find out which bug ships next — before your users do.
          </h1>

          <p className="mt-5 max-w-xl text-lg leading-relaxed text-secondary">
            crxes reads the logs you already collect and runs four AI agents over them in
            sequence: parse, detect patterns, trace root cause, predict. What comes back isn&apos;t
            another alert — it&apos;s a short list of the failures forming right now, with the
            confidence and the ETA attached.
          </p>

          <div className="mt-8">
            <OAuthButtons />
          </div>
          <p className="mt-4 font-mono text-code text-muted">
            Free tier · no credit card · read-only credentials, encrypted at rest
          </p>

          <div className="mt-10 flex flex-wrap items-center gap-x-6 gap-y-3">
            <span className="font-mono text-label uppercase text-muted">Connects to</span>
            {PROVIDERS.filter((p) => p.live).map(({ name, Mark }) => (
              <span key={name} className="flex items-center gap-2">
                <Mark className="h-5 w-5" />
                <span className="text-secondary">{name}</span>
              </span>
            ))}
          </div>
        </div>

        <HeroPanel />
      </div>
    </section>
  );
}

/** Static mock of a completed run — the product's payoff, shown not described. */
function HeroPanel() {
  const sev = SEVERITY_STYLE[SAMPLE_PREDICTION.severity];
  return (
    <div className="rounded-xl border border-border bg-surface shadow-2xl shadow-black/40">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <span className="font-mono text-code text-secondary">analysis · payments-api</span>
        <span className="flex items-center gap-1.5 font-mono text-label uppercase text-agent-4">
          <span className="h-1.5 w-1.5 rounded-full bg-agent-4" />
          complete
        </span>
      </div>

      <ol className="space-y-3 border-b border-border px-4 py-4">
        {AGENTS.map((agent) => (
          <li key={agent.key} className="flex items-center gap-3">
            <span
              className="h-2 w-2 shrink-0 rounded-full"
              style={{ backgroundColor: AGENT_COPY[agent.key].color }}
            />
            <span className="font-mono text-code text-primary">{agent.name}</span>
            <span
              className="h-px flex-1"
              style={{ backgroundColor: AGENT_COPY[agent.key].color, opacity: 0.28 }}
            />
            <span className="font-mono text-label text-muted">
              {[1.2, 3.4, 5.1, 4.6][agent.index]}s
            </span>
          </li>
        ))}
      </ol>

      <div className="px-4 py-4">
        <div className="flex items-center gap-2">
          <span
            className="rounded px-1.5 py-0.5 font-mono text-label uppercase"
            style={{ backgroundColor: `${sev.color}1f`, color: sev.color }}
          >
            {sev.label}
          </span>
          <span className="font-mono text-label text-muted">
            {Math.round(SAMPLE_PREDICTION.confidence * 100)}% confidence · ETA{" "}
            {SAMPLE_PREDICTION.eta}
          </span>
        </div>
        <h3 className="mt-2.5 text-title font-semibold">{SAMPLE_PREDICTION.title}</h3>
        <p className="mt-2 text-secondary">{SAMPLE_PREDICTION.rootCause}</p>
        <div className="mt-3 rounded-lg border border-border bg-elevated p-3">
          <Eyebrow>Recommended action</Eyebrow>
          <p className="mt-1 text-primary">{SAMPLE_PREDICTION.action}</p>
        </div>
      </div>
    </div>
  );
}

function HowItWorks() {
  return (
    <Section id="how">
      <Eyebrow>How it works</Eyebrow>
      <h2 className="mt-3 max-w-2xl text-3xl font-semibold tracking-tight">
        Three steps from credentials to a forecast.
      </h2>
      <div className="mt-12 grid gap-8 md:grid-cols-3">
        {STEPS.map((step) => (
          <div key={step.n} className="border-t border-border pt-5">
            <span className="font-mono text-label text-muted">{step.n}</span>
            <h3 className="mt-2 text-title font-semibold">{step.title}</h3>
            <p className="mt-2 text-secondary">{step.body}</p>
          </div>
        ))}
      </div>
    </Section>
  );
}

function Agents() {
  return (
    <Section id="agents">
      <Eyebrow>The pipeline</Eyebrow>
      <h2 className="mt-3 max-w-2xl text-3xl font-semibold tracking-tight">
        Four agents, each handing off to the next.
      </h2>
      <p className="mt-4 max-w-2xl text-secondary">
        One model asked to &ldquo;find bugs in these logs&rdquo; gives you a summary. Splitting the
        job into four specialists — each with a narrow brief and the previous one&apos;s output as
        its input — is what turns raw lines into a claim specific enough to act on.
      </p>

      <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
        {AGENTS.map((agent) => {
          const { color, blurb } = AGENT_COPY[agent.key];
          return (
            <div
              key={agent.key}
              className="relative overflow-hidden rounded-xl border border-border bg-surface p-5"
            >
              <span
                className="absolute inset-x-0 top-0 h-px"
                style={{ backgroundColor: color, opacity: 0.6 }}
              />
              <span className="font-mono text-label" style={{ color }}>
                AGENT {agent.index + 1}
              </span>
              <h3 className="mt-2 text-title font-semibold">{agent.name}</h3>
              <p className="mt-2 text-secondary">{blurb}</p>
            </div>
          );
        })}
      </div>

      <p className="mt-8 flex items-center gap-2 font-mono text-code text-muted">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-agent-2" />
        Progress streams over SSE — you watch each agent land, not a spinner.
      </p>
    </Section>
  );
}

function Sources() {
  return (
    <Section id="sources">
      <Eyebrow>Sources</Eyebrow>
      <h2 className="mt-3 max-w-2xl text-3xl font-semibold tracking-tight">
        Point it at the logs you already have.
      </h2>
      <p className="mt-4 max-w-2xl text-secondary">
        No agent to install, no shipper to reconfigure. Give crxes read-only credentials and it
        pulls on your schedule. Credentials are encrypted at rest and never leave your account.
      </p>

      <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {PROVIDERS.map(({ name, blurb, Mark, live }) => (
          <div
            key={name}
            className={`flex items-start gap-3.5 rounded-xl border border-border bg-surface p-5 ${
              live ? "" : "opacity-60"
            }`}
          >
            <Mark className="mt-0.5 h-7 w-7 shrink-0" />
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-title font-semibold">{name}</h3>
                {!live && (
                  <span className="rounded border border-border px-1.5 py-px font-mono text-label uppercase text-muted">
                    Soon
                  </span>
                )}
              </div>
              <p className="mt-1 text-secondary">{blurb}</p>
            </div>
          </div>
        ))}
      </div>
    </Section>
  );
}

function Features() {
  return (
    <Section>
      <Eyebrow>Once it&apos;s running</Eyebrow>
      <h2 className="mt-3 max-w-2xl text-3xl font-semibold tracking-tight">
        Built to run unattended, and to be checked afterward.
      </h2>
      <div className="mt-12 grid gap-x-10 gap-y-9 sm:grid-cols-2 lg:grid-cols-3">
        {FEATURES.map((f) => (
          <div key={f.title}>
            <h3 className="text-title font-semibold">{f.title}</h3>
            <p className="mt-2 text-secondary">{f.body}</p>
          </div>
        ))}
      </div>
    </Section>
  );
}

function Pricing() {
  return (
    <Section id="pricing">
      <Eyebrow>Pricing</Eyebrow>
      <h2 className="mt-3 max-w-2xl text-3xl font-semibold tracking-tight">
        Start free. Upgrade when the schedule matters.
      </h2>

      <div className="mt-12 grid gap-5 lg:grid-cols-2">
        <div className="rounded-xl border border-border bg-surface p-7">
          <h3 className="text-title font-semibold">Free</h3>
          <p className="mt-1 text-secondary">Enough to find out whether the forecasts hold up.</p>
          <p className="mt-6 text-stat font-semibold text-primary">$0</p>
          <ul className="mt-6 space-y-2.5">
            {[
              "1 connected source",
              "25 analyses per month",
              "Manual pulls",
              "7 days of history",
            ].map((item) => (
              <li key={item} className="flex gap-2.5 text-secondary">
                <span className="text-muted">·</span>
                {item}
              </li>
            ))}
          </ul>
          <div className="mt-7">
            <OAuthButtons size="sm" />
          </div>
        </div>

        <div className="relative rounded-xl border border-[#334155] bg-elevated p-7">
          <span className="absolute right-7 top-7 rounded border border-border px-2 py-0.5 font-mono text-label uppercase text-agent-4">
            Pro
          </span>
          <h3 className="text-title font-semibold">Pro</h3>
          <p className="mt-1 text-secondary">For sources you need watched around the clock.</p>
          <p className="mt-6 text-stat font-semibold text-primary">
            $29<span className="text-body font-normal text-muted"> / month</span>
          </p>
          <ul className="mt-6 space-y-2.5">
            {[
              "Unlimited sources",
              "Unlimited analyses",
              "Scheduled pulls from every 15 minutes",
              "Auto-analyze after each pull",
              "Full history and accuracy tracking",
            ].map((item) => (
              <li key={item} className="flex gap-2.5 text-secondary">
                <span className="text-agent-4">·</span>
                {item}
              </li>
            ))}
          </ul>
          <Link
            href="/login"
            className="mt-7 inline-flex rounded-lg bg-[#e2e8f0] px-5 py-3 font-medium text-[#0b1120] transition hover:bg-white"
          >
            Start on Free, upgrade in-app
          </Link>
        </div>
      </div>
    </Section>
  );
}

function ClosingCta() {
  return (
    <Section className="text-center">
      <h2 className="mx-auto max-w-2xl text-3xl font-semibold tracking-tight sm:text-4xl">
        Your logs already know what&apos;s about to break.
      </h2>
      <p className="mx-auto mt-4 max-w-xl text-secondary">
        Connect a source and get your first forecast in about two minutes.
      </p>
      <div className="mt-8 flex justify-center">
        <OAuthButtons />
      </div>
    </Section>
  );
}

function Footer() {
  return (
    <footer className="border-t border-border px-6 py-10">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 sm:flex-row">
        <Wordmark />
        <p className="font-mono text-code text-muted">
          Multi-agent bug prediction · {new Date().getFullYear()}
        </p>
      </div>
    </footer>
  );
}

/* -------------------------------------------------------------------------- */

export default function LandingPage() {
  return (
    <>
      <Nav />
      <main>
        <Hero />
        <HowItWorks />
        <Agents />
        <Sources />
        <Features />
        <Pricing />
        <ClosingCta />
      </main>
      <Footer />
    </>
  );
}
