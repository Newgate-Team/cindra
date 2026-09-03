export type UserRole = "agency" | "solo";
export type SocialPlatform = "telegram" | "instagram" | "facebook" | "tiktok";
export type GenerationContentType = "text" | "image" | "video";
export type AttachmentType = "image" | "video" | "audio" | "document";
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

export interface Attachment {
  url: string;
  attachment_type: AttachmentType;
  mime_type: string;
}

export interface SocialAccount {
  id: string;
  platform: SocialPlatform;
  external_account_id: string;
  display_name: string | null;
  token_expires_at: string | null;
  created_at: string;
}

export interface TikTokCreatorInfo {
  creator_username: string;
  creator_nickname: string;
  creator_avatar_url: string | null;
  privacy_level_options: string[];
  comment_disabled: boolean;
  duet_disabled: boolean;
  stitch_disabled: boolean;
  max_video_post_duration_sec: number;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface FeedItem {
  id: string;
  content_type: GenerationContentType;
  image_url: string | null;
  video_url: string | null;
  caption: string;
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

export interface BriefFile {
  filename: string;
  title: string;
  content: string;
}

export interface Illustration {
  prompt: string;
  status: GenerationStatus;
  image_url: string | null;
  error_message: string | null;
}

export interface VideoProject {
  id: string;
  topic: string;
  brand_guide: string | null;
  script: string | null;
  style: string | null;
  brief_files: BriefFile[] | null;
  illustrations: Illustration[] | null;
  video_url: string | null;
  video_status: "queued" | "processing" | "completed" | "failed" | "flagged" | null;
  video_error: string | null;
  status: "draft" | "script_ready" | "brief_ready" | "video_ready";
  created_at: string;
  updated_at: string;
}

export interface VideoStyle {
  id: string;
  title: string;
  description: string;
  produces: "brief" | "clip";
  generates_illustrations: boolean;
}

// CIN-143: catalog served by GET /content/image-templates.
export interface ImageTemplate {
  id: string;
  title: string;
  description: string;
  // CIN-150: null until staff have generated an example.
  preview_url: string | null;
}

// CIN-148: code-rendered layout templates, GET /content/layout-templates.
export interface LayoutSlot {
  name: string;
  label: string;
  max_length: number;
  required: boolean;
}

export interface LayoutTemplate {
  id: string;
  title: string;
  description: string;
  supports_image: boolean;
  slots: LayoutSlot[];
}
