import Link from "next/link";

import { SignOutButton } from "@/components/sign-out-button";
import { Wordmark } from "@/components/wordmark";

const NAV = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/analyze", label: "Analyze" },
];

/** Header + centered column shared by every signed-in page. */
export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen">
      <header className="border-b border-border px-6 py-4">
        <div className="mx-auto flex max-w-4xl items-center gap-6">
          <Wordmark href="/dashboard" />
          <nav className="flex items-center gap-5">
            {NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="text-secondary transition hover:text-primary"
              >
                {item.label}
              </Link>
            ))}
          </nav>
          <div className="ml-auto">
            <SignOutButton />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-4xl px-6 py-14">{children}</main>
    </div>
  );
}
