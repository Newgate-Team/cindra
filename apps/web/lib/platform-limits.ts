import type { SocialPlatform } from "./types";

// Caption limits the platforms enforce (or that our adapter cuts at).
// Facebook's is far above anything written by hand and Reddit's title
// rules differ from a body, so neither is checked. A post over the limit
// would otherwise be accepted here and fail at publish time.
const TEXT_LIMITS: Partial<Record<SocialPlatform, { text: number; withMedia?: number }>> = {
  telegram: { text: 4096, withMedia: 1024 },
  instagram: { text: 2200 },
  tiktok: { text: 2200 },
  youtube: { text: 5000 },
  linkedin: { text: 3000 },
  twitter: { text: 280 },
};

export function captionLimit(platform: SocialPlatform, hasMedia: boolean): number | null {
  const entry = TEXT_LIMITS[platform];
  if (!entry) return null;
  return hasMedia && entry.withMedia ? entry.withMedia : entry.text;
}

// The strictest limit among the chosen platforms, and which one sets it.
export function strictestLimit(
  platforms: SocialPlatform[],
  hasMedia: boolean
): { limit: number; platform: SocialPlatform } | null {
  let best: { limit: number; platform: SocialPlatform } | null = null;
  for (const platform of platforms) {
    const limit = captionLimit(platform, hasMedia);
    if (limit !== null && (best === null || limit < best.limit)) best = { limit, platform };
  }
  return best;
}
