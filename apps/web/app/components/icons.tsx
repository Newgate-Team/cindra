// Minimal inline stroke icons for the sidebar/bottom-tab navigation --
// hand-drawn rather than pulling in an icon library, matching this
// app's existing no-dependencies-beyond-next/react footprint.
type IconProps = { size?: number };

const common = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.75,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

export function LayoutIcon({ size = 20 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...common}>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M3 10h18M9 10v10" />
    </svg>
  );
}

export function WandIcon({ size = 20 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...common}>
      <path d="M4 20 15 9" />
      <path d="M13 3v3M20 6h-3M19 11l-1.5 1.5M8 3v2M5 6H3" />
    </svg>
  );
}

export function CalendarIcon({ size = 20 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...common}>
      <rect x="3.5" y="5" width="17" height="15" rx="2" />
      <path d="M3.5 9.5h17M8 3v3M16 3v3" />
    </svg>
  );
}

export function FeedIcon({ size = 20 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...common}>
      <rect x="3.5" y="3.5" width="7.5" height="7.5" rx="1.5" />
      <rect x="13" y="3.5" width="7.5" height="7.5" rx="1.5" />
      <rect x="3.5" y="13" width="7.5" height="7.5" rx="1.5" />
      <rect x="13" y="13" width="7.5" height="7.5" rx="1.5" />
    </svg>
  );
}

export function ShareIcon({ size = 20 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...common}>
      <circle cx="18" cy="5" r="2.5" />
      <circle cx="6" cy="12" r="2.5" />
      <circle cx="18" cy="19" r="2.5" />
      <path d="M8.3 10.7l7.4-4.4M8.3 13.3l7.4 4.4" />
    </svg>
  );
}

export function CardIcon({ size = 20 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...common}>
      <rect x="3" y="5.5" width="18" height="13" rx="2" />
      <path d="M3 10h18" />
      <path d="M6.5 14.5h4" />
    </svg>
  );
}

export function ClapperboardIcon({ size = 20 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...common}>
      <path d="M3.5 9.5 5 4.5l3 2-1.4 2.7M9 9.5l1.6-4.8 3 2-1.5 2.5M14.5 9.5l1.6-4.8 3 2-1.4 2.5" />
      <rect x="3.5" y="9.5" width="17" height="10" rx="1.5" />
    </svg>
  );
}

// Expand/collapse a truncated post's full text (CIN-83's PostText,
// Calendar) -- replaces the 🔍/🔼 emoji it used to render.
export function ChevronDownIcon({ size = 16 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...common}>
      <path d="M5 9l7 7 7-7" />
    </svg>
  );
}

export function ChevronUpIcon({ size = 16 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...common}>
      <path d="M5 15l7-7 7 7" />
    </svg>
  );
}
