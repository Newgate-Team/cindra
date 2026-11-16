import type { SubscriptionTier } from "./types";

// Grid rendering for the tier-comparison cards below -- kept as
// separate lookup tables (rather than reusing/reparsing TIER_LABELS)
// so the numbers stay a direct, readable mirror of app/plans.py's
// real PLAN_LIMITS (free: 20 text/3 image/0 video, 10 publications,
// 1 account; pro/business already matched what the upgrade copy
// below said before this page was restyled).
export const TIER_ORDER: SubscriptionTier[] = ["free", "pro", "business"];
export const TIER_NAME: Record<SubscriptionTier, string> = { free: "Free", pro: "Pro", business: "Business" };
export const TIER_PRICE: Record<SubscriptionTier, string> = { free: "0 $", pro: "19 $", business: "100 $" };
export const TIER_FEATURES: Record<SubscriptionTier, string[]> = {
  free: ["20 текстов, 3 фото в месяц", "10 публикаций в месяц", "1 подключённый аккаунт"],
  pro: ["300 текстов, 60 фото, 6 видео в месяц", "Без лимита публикаций", "Без лимита аккаунтов"],
  business: [
    "600 текстов, 150 фото, 55 видео в месяц",
    // CIN-146: the long-clip counter is separate from the video quota
    "3 длинных AI-ролика со звуком в месяц",
    "Без лимита публикаций",
    "Без лимита аккаунтов",
  ],
};
