import type { GenerationContentType, SocialPlatform } from "./types";

// TS mirror of apps/api/app/content_pipeline/publish_matrix.py -- kept
// in sync by hand, same as SocialPlatform/GenerationContentType
// themselves already are between schemas.py and this file's types.ts.
const ALLOWED_CONTENT_TYPES: Record<SocialPlatform, GenerationContentType[]> = {
  telegram: ["text", "image", "video"],
  facebook: ["text", "image", "video"],
  instagram: ["image", "video"],
  tiktok: ["video"],
  youtube: ["video"],
  // Video deliberately excluded -- see publish_matrix.py's comment on
  // the same entry (scoped-out follow-up, not yet implemented).
  linkedin: ["text", "image"],
  // Image/video go out as a Reddit "link" post pointing at Cindra's
  // own hosted URL, not a native asset upload -- see reddit.py's
  // module docstring and publish_matrix.py's comment on this entry.
  reddit: ["text", "image", "video"],
  // Text-only, deliberately -- see publish_matrix.py's comment on the
  // same entry (X's media upload isn't implemented at all here).
  twitter: ["text"],
};

const ALLOWED_CONTENT_KINDS: Record<SocialPlatform, Partial<Record<GenerationContentType, string[]>>> = {
  telegram: { text: ["post", "video_script"], image: ["post"], video: ["post"] },
  facebook: { text: ["post", "video_script"], image: ["post"], video: ["post"] },
  instagram: { image: ["post", "story"], video: ["post", "story"] },
  tiktok: { video: ["post"] },
  youtube: { video: ["post"] },
  linkedin: { text: ["post", "video_script"], image: ["post"] },
  reddit: { text: ["post", "video_script"], image: ["post"], video: ["post"] },
  twitter: { text: ["post", "video_script"] },
};

export const CONTENT_KIND_LABELS: Record<string, string> = {
  post: "Пост",
  story: "Сторис",
  video_script: "Сценарий видео",
};

const ALL_CONTENT_TYPES: GenerationContentType[] = ["text", "image", "video"];

export function allowedContentTypesFor(platforms: SocialPlatform[]): GenerationContentType[] {
  if (platforms.length === 0) return [];
  return ALL_CONTENT_TYPES.filter((ct) => platforms.every((p) => ALLOWED_CONTENT_TYPES[p].includes(ct)));
}

export function allowedContentKindsFor(
  platforms: SocialPlatform[],
  contentType: GenerationContentType
): string[] {
  if (platforms.length === 0) return [];
  const [first, ...rest] = platforms.map((p) => new Set(ALLOWED_CONTENT_KINDS[p][contentType] ?? []));
  return [...first].filter((kind) => rest.every((s) => s.has(kind)));
}
