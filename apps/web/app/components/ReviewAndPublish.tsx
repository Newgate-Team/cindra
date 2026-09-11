"use client";

import { useMemo, useState, type FormEvent } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { allowedContentKindsFor, allowedContentTypesFor } from "@/lib/publish-matrix";
import type { GenerationContentType, Post, SocialAccount } from "@/lib/types";

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
// A connected social account is only required here, at actual publish
// time -- not to generate. `targetAccountIds` is what the caller
// already had picked (locked in before generation, CIN-106, when it
// chose to offer that step); when it's empty -- no account picked yet,
// or none existed when generation ran -- this component asks for one
// itself, filtered to whatever can actually publish this specific
// content_type/content_kind, instead of silently letting the account
// choice vanish.
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
  // Own picker only kicks in when the caller didn't already lock in a
  // target -- otherwise this mirrors exactly what it always did.
  const [pickedAccountIds, setPickedAccountIds] = useState<string[]>([]);
  const needsOwnPicker = targetAccountIds.length === 0;
  const contentType: GenerationContentType = videoUrl ? "video" : imageUrl ? "image" : "text";
  const eligibleAccounts = useMemo(
    () =>
      needsOwnPicker
        ? accounts.filter(
            (a) =>
              allowedContentTypesFor([a.platform]).includes(contentType) &&
              allowedContentKindsFor([a.platform], contentType).includes(contentKind)
          )
        : [],
    [needsOwnPicker, accounts, contentType, contentKind]
  );
  const effectiveAccountIds = needsOwnPicker ? pickedAccountIds : targetAccountIds;
  const targetAccounts = useMemo(
    () => accounts.filter((account) => effectiveAccountIds.includes(account.id)),
    [accounts, effectiveAccountIds]
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
          social_account_ids: effectiveAccountIds,
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
      {needsOwnPicker ? (
        eligibleAccounts.length === 0 ? (
          <p className="muted">
            Чтобы опубликовать, подключите соцсеть на странице «Соцсети» — там нужен минимум один
            подходящий аккаунт.
          </p>
        ) : (
          <fieldset className="chip-group">
            <legend>Куда опубликовать</legend>
            {eligibleAccounts.map((a) => (
              <label key={a.id}>
                <input
                  type="checkbox"
                  checked={pickedAccountIds.includes(a.id)}
                  onChange={(e) =>
                    setPickedAccountIds(
                      e.target.checked
                        ? [...pickedAccountIds, a.id]
                        : pickedAccountIds.filter((id) => id !== a.id)
                    )
                  }
                />
                {a.platform} — {a.display_name ?? a.external_account_id}
              </label>
            ))}
          </fieldset>
        )
      ) : (
        <p>
          Куда опубликовать:{" "}
          {targetAccounts.map((a) => `${a.platform} — ${a.display_name ?? a.external_account_id}`).join(", ")}
        </p>
      )}
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
      <button
        type="submit"
        disabled={publishing || tiktok.loading || !tiktok.ready || effectiveAccountIds.length === 0}
      >
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
