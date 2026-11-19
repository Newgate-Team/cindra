"use client";

import { Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";

import { CREATE_ACTIONS, NAV_SECTIONS } from "@/lib/nav";

import { SectionTile } from "./Brand";

interface Entry {
  key: string;
  href: string;
  label: string;
  hint: string;
  icon: (typeof NAV_SECTIONS)[number]["icon"];
  color: string;
}

const ENTRIES: Entry[] = [
  ...CREATE_ACTIONS.map((a) => ({
    key: `create:${a.href}`,
    href: a.href,
    label: `Создать: ${a.label}`,
    hint: a.hint,
    icon: a.icon,
    color: a.color,
  })),
  ...NAV_SECTIONS.map((s) => ({
    key: `go:${s.href}`,
    href: s.href,
    label: s.label,
    hint: "Перейти",
    icon: s.icon,
    color: s.color,
  })),
];

// ⌘K / Ctrl+K: jump to a section or start something new without
// reaching for the menu. Arrow keys move, Enter opens, Esc closes.
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);

  const results = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return needle ? ENTRIES.filter((e) => e.label.toLowerCase().includes(needle)) : ENTRIES;
  }, [query]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setIndex(0);
      inputRef.current?.focus();
    }
  }, [open]);

  if (!open) return null;

  function go(entry: Entry | undefined) {
    if (!entry) return;
    onClose();
    router.push(entry.href);
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape") {
      onClose();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setIndex((i) => Math.min(i + 1, results.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setIndex((i) => Math.max(i - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      go(results[index]);
    }
  }

  return (
    <div className="palette-backdrop" onMouseDown={onClose}>
      <div
        className="palette"
        role="dialog"
        aria-modal="true"
        aria-label="Быстрый переход"
        onMouseDown={(event) => event.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <label className="palette-search">
          <Search size={18} aria-hidden="true" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setIndex(0);
            }}
            placeholder="Куда перейти или что создать?"
            aria-label="Быстрый переход"
            role="combobox"
            aria-expanded="true"
            aria-controls="palette-list"
            aria-activedescendant={results[index] ? `palette-${results[index].key}` : undefined}
          />
        </label>
        <ul id="palette-list" role="listbox" className="palette-list">
          {results.length === 0 && <li className="palette-empty">Ничего не нашли — попробуйте другое слово</li>}
          {results.map((entry, i) => (
            <li
              key={entry.key}
              id={`palette-${entry.key}`}
              role="option"
              aria-selected={i === index}
              className={i === index ? "selected" : undefined}
              onMouseEnter={() => setIndex(i)}
              onClick={() => go(entry)}
              style={{ "--t": entry.color } as CSSProperties}
            >
              <SectionTile section={entry} />
              <span className="palette-label">{entry.label}</span>
              <small>{entry.hint}</small>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
