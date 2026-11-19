import type { ReactNode } from "react";

// Building blocks shared by the studio screens (Посты, Видео, Аватар,
// Монтаж, Шаблоны): the redesign draws every one of them as "controls
// on the left, a live preview on the right", and the same chip row and
// social-style card keep them from drifting apart.

export interface ChipOption<T extends string> {
  value: T;
  label: string;
}

// Single choice as a row of chips. Real <button>s with aria-pressed, so
// it is keyboard- and screen-reader-friendly without a radio group's
// visual baggage.
export function ChipSelect<T extends string>({
  label,
  options,
  value,
  onChange,
  disabled = false,
}: {
  label: string;
  options: ChipOption<T>[];
  value: T;
  onChange: (value: T) => void;
  disabled?: boolean;
}) {
  return (
    <div className="field-block" role="group" aria-label={label}>
      <span className="field-label">{label}</span>
      <div className="chip-row">
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            className={`chip${option.value === value ? " selected" : ""}`}
            aria-pressed={option.value === value}
            disabled={disabled}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

const PLATFORM_LABELS: Record<string, string> = {
  telegram: "Telegram",
  instagram: "Instagram",
  facebook: "Facebook",
  linkedin: "LinkedIn",
  reddit: "Reddit",
  twitter: "X",
  tiktok: "TikTok",
  youtube: "YouTube",
};

export function platformLabel(platform: string): string {
  return PLATFORM_LABELS[platform] ?? platform;
}

// A post as it will look in a feed: author row, media, caption. The
// media slot takes whatever the caller has (the generated picture, a
// video player, or a poster placeholder), so the same frame serves the
// "example" state before generation and the real result after it.
export function PostCard({
  platform,
  author,
  media,
  children,
}: {
  platform: string;
  author: string;
  media?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className={`post post-${platform}`}>
      <div className="post-head">
        <span className="avatar" aria-hidden="true">
          {author.trim().charAt(0).toUpperCase() || "C"}
        </span>
        <div>
          <b>{author}</b>
          <small>{platformLabel(platform)} · сейчас</small>
        </div>
      </div>
      {media}
      {children}
    </div>
  );
}
