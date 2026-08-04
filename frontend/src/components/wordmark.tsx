import Link from "next/link";

const AGENT_COLORS = ["var(--agent-1)", "var(--agent-2)", "var(--agent-3)", "var(--agent-4)"];

export function Wordmark({ href = "/" }: { href?: string }) {
  return (
    <Link href={href} className="flex items-center gap-2.5">
      <span className="grid grid-cols-2 gap-[3px]" aria-hidden="true">
        {AGENT_COLORS.map((c) => (
          <span key={c} className="h-[7px] w-[7px] rounded-[2px]" style={{ backgroundColor: c }} />
        ))}
      </span>
      <span className="font-mono text-title font-semibold tracking-tight text-primary">crxes</span>
    </Link>
  );
}
