export type UserRole = "agency" | "solo";
export type SocialPlatform = "telegram" | "instagram" | "facebook" | "tiktok" | "youtube";
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
  // Лента (CIN-109) is a shared feed by design -- this controls whether
  // this user's own completed image/video generations appear in it.
  // Defaults by role at registration (agency: false, solo: true);
  // editable on /settings, PATCH /auth/me accepts it directly.
  share_generations_to_feed: boolean;
  // False for a Google-only account (no password exists to change) --
  // /settings uses this to decide whether to show that form at all.
  has_password: boolean;
  // False until confirmed via a mailed link, or true immediately for a
  // Google-created/-linked account. Informational only -- nothing in
  // the product is gated on this.
  email_verified: boolean;
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

export interface TeamMember {
  id: string;
  email: string;
  role: UserRole;
  is_owner: boolean;
}

export interface Team {
  id: string;
  name: string;
  owner_user_id: string;
  members: TeamMember[];
  created_at: string;
}

export interface ABTest {
  id: string;
  topic: string;
  winner_generation_job_id: string | null;
  variants: GenerationJob[];
  created_at: string;
}

export interface PlatformPostStats {
  platform: SocialPlatform;
  published: number;
  failed: number;
}

export interface ContentKindCount {
  content_kind: string;
  count: number;
}

export interface DailyPostCount {
  day: string;
  published: number;
}

export interface ContentTypeGenerationStats {
  content_type: GenerationContentType;
  completed: number;
  failed: number;
}

export interface UsageLimitStat {
  event_type: string;
  content_type: GenerationContentType | null;
  label: string;
  used: number;
  limit: number | null;
}

export interface AnalyticsSummary {
  period_days: number;
  posts_total: number;
  posts_scheduled: number;
  posts_publishing: number;
  posts_published: number;
  posts_failed: number;
  publish_success_rate: number | null;
  posts_by_platform: PlatformPostStats[];
  posts_by_content_kind: ContentKindCount[];
  daily_published_posts: DailyPostCount[];
  generations_total: number;
  generations_queued: number;
  generations_processing: number;
  generations_completed: number;
  generations_failed: number;
  generations_flagged: number;
  generation_success_rate: number | null;
  generations_by_content_type: ContentTypeGenerationStats[];
  usage_this_period: UsageLimitStat[];
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
