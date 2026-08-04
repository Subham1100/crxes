"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { API_URL } from "@/lib/api";

export function SignOutButton() {
  const router = useRouter();
  const [pending, setPending] = useState(false);

  async function signOut() {
    setPending(true);
    try {
      await fetch(`${API_URL}/api/auth/logout`, { method: "POST", credentials: "include" });
    } finally {
      router.push("/");
      router.refresh();
    }
  }

  return (
    <button
      onClick={signOut}
      disabled={pending}
      className="rounded-lg border border-border bg-surface px-3.5 py-2 text-secondary transition hover:text-primary disabled:opacity-60"
    >
      {pending ? "Signing out…" : "Sign out"}
    </button>
  );
}
