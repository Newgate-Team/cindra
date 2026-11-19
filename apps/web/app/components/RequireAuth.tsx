"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useAuth } from "@/lib/auth-context";

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (loading || user) return;
    // Back to where they were after logging in (e.g. the session expired
    // mid-work). Query string included; it never leaves this origin.
    const here = pathname + window.location.search;
    router.replace(here === "/" ? "/login" : `/login?next=${encodeURIComponent(here)}`);
  }, [loading, user, router, pathname]);

  if (loading) return <p className="muted">Загрузка…</p>;
  if (!user) return null;
  return <>{children}</>;
}
