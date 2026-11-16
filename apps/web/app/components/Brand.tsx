import { Flame } from "lucide-react";
import Link from "next/link";
import type { CSSProperties } from "react";

import type { NavSection } from "@/lib/nav";

export function Logo() {
  return (
    <Link href="/" className="logo" aria-label="Cindra — на главную">
      <span className="flame" aria-hidden="true">
        <Flame size={22} strokeWidth={2.2} />
      </span>
      <span className="logo-word">
        cindra<span className="logo-dot">.</span>
      </span>
    </Link>
  );
}

// A section's icon on its own colour square -- the menu, the ⌘K palette
// and the "Ещё" sheet all draw it the same way, like iOS Settings.
export function SectionTile({ section, size = 17 }: { section: Pick<NavSection, "icon" | "color">; size?: number }) {
  const Icon = section.icon;
  return (
    <span className="tile" style={{ "--t": section.color } as CSSProperties} aria-hidden="true">
      <Icon size={size} strokeWidth={2} />
    </span>
  );
}

// Decorative card with a big phrase: the landing's showcase and the
// side of the sign-in pages. Purely visual (the shapes are CSS).
export function Poster({ title, tone = "violet" }: { title: string; tone?: "violet" | "sun" | "leaf" }) {
  return (
    <div className={`poster ${tone}`}>
      <small>CINDRA STUDIO</small>
      <b>{title}</b>
      <small>СОЗДАНО С ИСКРОЙ</small>
    </div>
  );
}
