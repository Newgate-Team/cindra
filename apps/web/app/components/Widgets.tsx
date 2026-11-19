import type { LucideIcon } from "lucide-react";
import type { CSSProperties } from "react";

import type { UsageLimitStat } from "@/lib/types";

// Small display pieces the dashboard and the analytics page share.

// A ring's colour follows what it counts, the same colours the sections wear.
export function usageColor(stat: UsageLimitStat): string {
  if (stat.content_type === "image") return "#ed4c88";
  if (stat.content_type === "video") return "#ef9651";
  if (stat.content_type === "text") return "#7c5cff";
  return "#14a995";
}

export function Ring({ value, of, color }: { value: number; of: number; color: string }) {
  const percent = of > 0 ? Math.min(100, Math.round((value / of) * 100)) : 0;
  // Amber from 80% and red at the limit: the colour warns before the 402 does.
  const tone = percent >= 100 ? "#e5484d" : percent >= 80 ? "#f59e0b" : color;
  return (
    <span className="ring" style={{ "--p": percent, "--t": tone } as CSSProperties} role="img" aria-label={`${percent}% лимита`}>
      <b>{value}</b>
    </span>
  );
}

export function Metric({ icon: Icon, label, value, note, tint }: { icon: LucideIcon; label: string; value: string; note: string; tint: string }) {
  return (
    <div className="metric" style={{ "--soft": tint } as CSSProperties}>
      <div className="metric-top">
        <Icon size={16} aria-hidden="true" />
        {label}
      </div>
      <strong className="number">{value}</strong>
      <span className="metric-note">{note}</span>
    </div>
  );
}

