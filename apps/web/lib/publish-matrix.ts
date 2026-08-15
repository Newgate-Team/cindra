import type { GenerationContentType, SocialPlatform } from "./types";

// TS mirror of apps/api/app/content_pipeline/publish_matrix.py -- kept
// in sync by hand, same as SocialPlatform/GenerationContentType
// themselves already are between schemas.py and this file's types.ts.
const ALLOWED_CONTENT_TYPES: Record<SocialPlatform, GenerationContentType[]> = {
  telegram: ["text", "image", "video"],
  facebook: ["text", "image", "video"],
  instagram: ["image", "video"],
  tiktok: ["video"],
};

const ALLOWED_CONTENT_KINDS: Record<SocialPlatform, Partial<Record<GenerationContentType, string[]>>> = {
  telegram: { text: ["post", "video_script"], image: ["post"], video: ["post"] },
  facebook: { text: ["post", "video_script"], image: ["post"], video: ["post"] },
  instagram: { image: ["post", "story"], video: ["post", "story"] },
  tiktok: { video: ["post"] },
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
