"use client";

import { Plus, Search } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, type CSSProperties } from "react";

import { useAuth } from "@/lib/auth-context";
import { CREATE_ACTIONS, sectionFor } from "@/lib/nav";

import { Logo, SectionTile } from "./Brand";
import { CommandPalette } from "./CommandPalette";

function CreateMenu() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onPointer(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="create-menu" ref={rootRef}>
      <button
        type="button"
        className="primary"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <Plus size={17} /> <span className="create-label">Создать</span>
      </button>
      {open && (
        <div className="create-popover" role="menu">
          {CREATE_ACTIONS.map((action) => (
            <button
              key={action.href}
              type="button"
              role="menuitem"
              className="create-item"
              style={{ "--t": action.color } as CSSProperties}
              onClick={() => {
                setOpen(false);
                router.push(action.href);
              }}
            >
              <SectionTile section={action} />
              <span>
                <b>{action.label}</b>
                <small>{action.hint}</small>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function Topbar() {
  const { user, loading } = useAuth();
  const pathname = usePathname();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const current = sectionFor(pathname);
  const signedIn = Boolean(user);

  useEffect(() => {
    if (!signedIn) return;
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((v) => !v);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [signedIn]);

  // Pages are client components, so they cannot export `metadata`; the
  // section name in the browser tab and history comes from here.
  useEffect(() => {
    if (signedIn && current) document.title = `${current.label} — Cindra`;
  }, [signedIn, current]);

  // Keeps the page from jumping while the session is being checked.
  if (loading) return <header className="topbar" aria-hidden="true" />;

  if (!user) {
    return (
      <header className="topbar topbar-public">
        <Logo />
        <div className="topbar-actions">
          <Link href="/login" className="button-link secondary">
            Войти
          </Link>
          <Link href="/register" className="button-link primary">
            Начать бесплатно
          </Link>
        </div>
      </header>
    );
  }

  return (
    <header className="topbar">
      <div className="topbar-mobile-logo">
        <Logo />
      </div>
      <nav className="crumbs" aria-label="Где вы">
        <span>Ваша студия</span>
        {current && (
          <>
            <span aria-hidden="true">/</span>
            <b>{current.label}</b>
          </>
        )}
      </nav>
      <div className="topbar-actions">
        <button type="button" className="secondary search-button" onClick={() => setPaletteOpen(true)} aria-label="Быстрый переход">
          <Search size={17} />
          <kbd>⌘ K</kbd>
        </button>
        <CreateMenu />
      </div>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </header>
  );
}
