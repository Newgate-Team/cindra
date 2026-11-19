"use client";

import type { ReactNode } from "react";

import { useAuth } from "@/lib/auth-context";

import { NavBar } from "./NavBar";
import { Topbar } from "./Topbar";

// The frame around every page. `data-auth` lets the stylesheet drop the
// sidebar for visitors (landing, login, legal pages get the full width)
// without every page having to know about it.
export function AppShell({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const mode = loading ? "loading" : user ? "in" : "out";

  return (
    <div className="app-shell" data-auth={mode}>
      <NavBar />
      <div className="app-content">
        <Topbar />
        {children}
      </div>
    </div>
  );
}
