"use client";

import { useMemo, useState, type FormEvent } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { Post, SocialAccount } from "@/lib/types";

import { TikTokPublishFields, useTikTokPublishOptions } from "./TikTokPublishFields";

// datetime-local reads and writes LOCAL time, so the `min` guard has
// to be local too. The version this was extracted from used
// toISOString() directly (UTC), which east of Greenwich left a window
// where the picker still accepted a past moment -- five hours of it in
// UTC+5. Fixed here rather than carried over.
function minDatetimeLocal(): string {
  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  return now.toISOString().slice(0, 16);
}

// The review/edit-before-publish step (CIN-38): once content is ready,
// it becomes an editable draft here rather than on a separate screen --
// reviewing what you just made is part of the same flow.
//
// Extracted from GenerationForm in CIN-149 and given explicit media
// props instead of a GenerationJob, so the template studio -- which
// renders an image synchronously and has no job at all -- publishes
// through exactly the same component rather than a copy of it. Same
// reasoning as pulling TikTokPublishFields out in CIN-136.
//
// Target accounts are not chosen here (CIN-106) -- they were locked in
// before generation, since content_type/content_kind were already
// validated against what those specific accounts can publish.
export function ReviewAndPublish({
  imageUrl,
  videoUrl,
  generatedText,
  generationJobId = null,
  contentKind,
  initialCaption,
  accounts,
  targetAccountIds,
  // Layout renders (CIN-148) place the user's own text with code, so
  // they aren't AI-generated media the way a Veo clip is.
  aiGenerated = true,
}: {
  imageUrl?: string | null;
  videoUrl?: string | null;
  generatedText?: string | null;
  generationJobId?: string | null;
  contentKind: string;
  initialCaption: string;
  accounts: SocialAccount[];
  targetAccountIds: string[];
  aiGenerated?: boolean;
}) {
  const { token } = useAuth();
  const [text, setText] = useState(generatedText ?? initialCaption);
  const [scheduledFor, setScheduledFor] = useState("");
  const [posts, setPosts] = useState<Post[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [publishing, setPublishing] = useState(false);
  const targetAccounts = useMemo(
    () => accounts.filter((account) => targetAccountIds.includes(account.id)),
    [accounts, targetAccountIds]
  );
  const targetTikTokAccounts = useMemo(
    () => targetAccounts.filter((account) => account.platform === "tiktok"),
    [targetAccounts]
  );
  const tiktok = useTikTokPublishOptions(targetTikTokAccounts, token, { aiGenerated });

  async function handlePublish(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setPublishing(true);
    try {
      const created = await api.post<Post[]>(
        "/posts",
        {
          social_account_ids: targetAccountIds,
          text,
          image_url: imageUrl ?? null,
          video_url: videoUrl ?? null,
          content_kind: contentKind,
          generation_job_id: generationJobId,
          scheduled_for: scheduledFor ? new Date(scheduledFor).toISOString() : null,
          platform_options: tiktok.platformOptions,
        },
        token
      );
      setPosts(created);
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        setError("Лимит публикаций по тарифу исчерпан.");
      } else if (err instanceof ApiError) {
        setError(err.message);
      } else {
        // Not an ApiError -- the request never got a proper response at
        // all (network failure, CORS, backend restart mid-request), as
        // opposed to every other publish failure in this app, which
        // comes back as a specific backend detail message (CIN-120).
        // Surface the real message instead of a generic string so the
        // next report is diagnosable without a live repro.
        setError(err instanceof Error ? `Не удалось опубликовать: ${err.message}` : "Не удалось опубликовать");
      }
    } finally {
      setPublishing(false);
    }
  }

  return (
    <form onSubmit={handlePublish}>
      {imageUrl && (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={imageUrl} alt="Изображение для публикации" style={{ maxWidth: "100%", borderRadius: 8 }} />
      )}
      {videoUrl && <video src={videoUrl} controls style={{ maxWidth: "100%", borderRadius: 8 }} />}
      <label>
        {imageUrl || videoUrl ? "Подпись (можно отредактировать перед публикацией)" : "Текст (можно отредактировать перед публикацией)"}
        <textarea rows={6} value={text} onChange={(e) => setText(e.target.value)} />
      </label>
      <p>
        Куда опубликовать:{" "}
        {targetAccounts.map((a) => `${a.platform} — ${a.display_name ?? a.external_account_id}`).join(", ")}
      </p>
      <TikTokPublishFields
        accounts={targetTikTokAccounts}
        creators={tiktok.creators}
        options={tiktok.options}
        onChange={tiktok.updateOption}
        loading={tiktok.loading}
        error={tiktok.error}
        aigcLocked={aiGenerated}
      />
      <label>
        Запланировать на (необязательно — иначе публикуем сразу)
        <input
          type="datetime-local"
          min={minDatetimeLocal()}
          value={scheduledFor}
          onChange={(e) => setScheduledFor(e.target.value)}
        />
      </label>
      {error && <p className="error">{error}</p>}
      <button type="submit" disabled={publishing || tiktok.loading || !tiktok.ready}>
        {publishing ? "Публикуем…" : scheduledFor ? "Запланировать" : "Опубликовать сейчас"}
      </button>
      {posts && (
        <div>
          {posts.map((post) => (
            <div key={post.id} className="card list-row">
              <div className="list-row-body">
                <strong>{post.platform}</strong>
                <p className="muted list-row-meta">
                  <span>{post.account_label}</span>
                </p>
                {post.status === "failed" && post.error_message && (
                  <p className="error">{post.error_message}</p>
                )}
              </div>
              <div className="list-row-side">
                <span className={`badge ${post.status}`}>{post.status}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </form>
  );
}
