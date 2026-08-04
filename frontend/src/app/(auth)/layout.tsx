import { redirect } from "next/navigation";

import { Wordmark } from "@/components/wordmark";
import { getSession } from "@/lib/session";

/** Shell for /login and /signup — signed-in visitors get bounced to the app. */
export default async function AuthLayout({ children }: { children: React.ReactNode }) {
  if (await getSession()) redirect("/dashboard");

  return (
    <div className="relative flex min-h-screen flex-col">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[420px] opacity-60"
        style={{
          background:
            "radial-gradient(55% 55% at 50% 0%, rgba(59,130,246,0.16) 0%, transparent 70%)",
        }}
      />
      <header className="px-6 py-6">
        <div className="mx-auto max-w-5xl">
          <Wordmark />
        </div>
      </header>
      <main className="flex flex-1 items-center justify-center px-6 pb-20">{children}</main>
    </div>
  );
}
