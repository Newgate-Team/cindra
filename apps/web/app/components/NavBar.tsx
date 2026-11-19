"use client";

import { Ellipsis, LogOut, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { BOTTOM_TABS, NAV_GROUPS, NAV_SECTIONS, sectionFor } from "@/lib/nav";
import type { Subscription } from "@/lib/types";

import { Logo, SectionTile } from "./Brand";

const TIER_LABELS: Record<string, string> = { free: "FREE", pro: "PRO", business: "BUSINESS" };

// The plan badge on the workspace card. A lapsed subscription shows as
// FREE, like the quota checks treat it (effective_tier in the API).
function useTierLabel(): string | null {
  const { user, token } = useAuth();
  const [label, setLabel] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    api
      .get<Subscription>("/billing/subscription", token)
      .then((sub) => setLabel(TIER_LABELS[sub.status === "active" ? sub.tier : "free"] ?? null))
      .catch(() => {});
  }, [user, token]);

  return label;
}

function userInitial(email: string): string {
  return (email.trim()[0] ?? "?").toUpperCase();
}

function Sidebar() {
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const tier = useTierLabel();
  const current = sectionFor(pathname);

  if (!user) return null;

  function handleLogout() {
    logout();
    router.push("/login");
  }

  return (
    <aside className="sidebar">
      <Logo />
      <div className="workspace">
        <span className="workspace-name" title={user.email}>
          {user.role === "agency" ? "Агентство" : "Личное пространство"}
        </span>
        {tier && <span className="badge plan">{tier}</span>}
      </div>
      <nav className="sidebar-nav" aria-label="Разделы Cindra">
        {NAV_GROUPS.map((group) => (
          <div key={group.title || "top"} className="nav-group">
            {group.title && <div className="nav-group-title">{group.title}</div>}
            {group.sections.map((section) => (
              <Link
                key={section.href}
                href={section.href}
                className={current?.href === section.href ? "active" : undefined}
                aria-current={current?.href === section.href ? "page" : undefined}
                style={{ "--t": section.color } as React.CSSProperties}
              >
                <SectionTile section={section} />
                <span className="nav-label">{section.label}</span>
              </Link>
            ))}
          </div>
        ))}
      </nav>
      <div className="sidebar-user">
        <span className="avatar" aria-hidden="true">
          {userInitial(user.email)}
        </span>
        <div className="sidebar-user-text">
          <b title={user.email}>{user.email}</b>
          <small>{user.email_verified ? "Email подтверждён" : "Email не подтверждён"}</small>
        </div>
        <button type="button" className="icon-button ghost" onClick={handleLogout} aria-label="Выйти" title="Выйти">
          <LogOut size={17} />
        </button>
      </div>
    </aside>
  );
}

// Phone: five slots, the last one opens the rest as a grid of coloured tiles.
function BottomTabs() {
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const [moreOpen, setMoreOpen] = useState(false);
  const current = sectionFor(pathname);

  // Navigating (a tile in the sheet, the browser's back) closes the sheet.
  useEffect(() => setMoreOpen(false), [pathname]);

  useEffect(() => {
    if (!moreOpen) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setMoreOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [moreOpen]);

  if (!user) return null;

  const tabs = BOTTOM_TABS.map((href) => NAV_SECTIONS.find((s) => s.href === href)!).filter(Boolean);
  const moreActive = current !== undefined && !BOTTOM_TABS.includes(current.href);

  function handleLogout() {
    logout();
    router.push("/login");
  }

  return (
    <>
      <nav className="bottom-tabs" aria-label="Основные разделы">
        {tabs.map((section) => {
          const Icon = section.icon;
          const active = current?.href === section.href;
          return (
            <Link
              key={section.href}
              href={section.href}
              className={active ? "active" : undefined}
              aria-current={active ? "page" : undefined}
              style={{ "--t": section.color } as React.CSSProperties}
            >
              <Icon size={21} strokeWidth={active ? 2.3 : 1.9} />
              <span>{section.label}</span>
            </Link>
          );
        })}
        <button
          type="button"
          className={`tab-more${moreActive ? " active" : ""}`}
          onClick={() => setMoreOpen(true)}
          aria-haspopup="dialog"
          aria-expanded={moreOpen}
        >
          <Ellipsis size={21} />
          <span>Ещё</span>
        </button>
      </nav>

      {moreOpen && (
        <div className="sheet-backdrop" onClick={() => setMoreOpen(false)}>
          <section
            className="sheet"
            role="dialog"
            aria-modal="true"
            aria-label="Все разделы"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="sheet-head">
              <h2>Все разделы</h2>
              <button type="button" className="icon-button ghost" onClick={() => setMoreOpen(false)} aria-label="Закрыть">
                <X size={18} />
              </button>
            </div>
            <div className="sheet-grid">
              {NAV_SECTIONS.map((section) => (
                <Link key={section.href} href={section.href} style={{ "--t": section.color } as React.CSSProperties}>
                  <SectionTile section={section} size={22} />
                  <span>{section.label}</span>
                </Link>
              ))}
            </div>
            <div className="sheet-foot">
              <small title={user.email}>{user.email}</small>
              <button type="button" className="secondary" onClick={handleLogout}>
                <LogOut size={16} /> Выйти
              </button>
            </div>
          </section>
        </div>
      )}
    </>
  );
}

export function NavBar() {
  return (
    <>
      <Sidebar />
      <BottomTabs />
    </>
  );
}
