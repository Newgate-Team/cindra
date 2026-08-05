export type UserRole = "agency" | "solo";
export type SocialPlatform = "telegram" | "instagram" | "facebook";
export type GenerationContentType = "text" | "image" | "video";
export type GenerationStatus = "queued" | "processing" | "completed" | "failed" | "flagged";
export type PostStatus = "scheduled" | "publishing" | "published" | "failed";
export type SubscriptionTier = "free" | "pro" | "business";
export type SubscriptionStatus = "active" | "past_due" | "canceled";

export interface User {
  id: string;
  email: string;
  role: UserRole;
  created_at: string;
}

export interface Subscription {
  tier: SubscriptionTier;
  status: SubscriptionStatus;
  current_period_end: string | null;
}

export interface GenerationJob {
  id: string;
  content_type: GenerationContentType;
  status: GenerationStatus;
  output_payload: { text?: string; image_url?: string; video_url?: string; prompt?: string } | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface SocialAccount {
  id: string;
  platform: SocialPlatform;
  external_account_id: string;
  display_name: string | null;
  token_expires_at: string | null;
  created_at: string;
}

export interface Post {
  id: string;
  social_account_id: string;
  text: string;
  image_url: string | null;
  video_url: string | null;
  content_kind: string;
  status: PostStatus;
  scheduled_for: string;
  platform_message_id: string | null;
  error_message: string | null;
  created_at: string;
  published_at: string | null;
  platform: SocialPlatform;
  account_label: string;
}
