"use client";

import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { CONTENT_KIND_LABELS, allowedContentKindsFor, allowedContentTypesFor } from "@/lib/publish-matrix";
import type {
  Attachment,
  GenerationContentType,
  GenerationJob,
  Post,
  SocialAccount,
  SocialPlatform,
} from "@/lib/types";

import { RequireAuth } from "../components/RequireAuth";

const TERMINAL_STATUSES = new Set(["completed", "failed", "flagged"]);
const POLL_INTERVAL_MS = 2000;

// datetime-local's `min` needs "yyyy-MM-ddTHH:mm" -- called fresh on
// every render rather than a module-level constant, so it stays "now"
// across a long-lived page instance instead of freezing at page load.
function minDatetimeLocal(): string {
  return new Date().toISOString().slice(0, 16);
}

function platformsFor(ids: string[], accounts: SocialAccount[]): SocialPlatform[] {
  const set = new Set<SocialPlatform>();
  for (const id of ids) {
    const account = accounts.find((a) => a.id === id);
    if (account) set.add(account.platform);
  }
  return [...set];
}

// The review/edit-before-publish step (CIN-38): once generation
// completes, the raw text becomes an editable draft here rather than
// a separate screen -- reviewing what you just generated is part of
// the same flow, not a different destination.
//
// Target accounts are no longer chosen here (CIN-106) -- they were
// locked in before generation, since content_type/content_kind were
// already validated against what those specific accounts can publish.
// Changing targets after the fact could land on an invalid combo
// without regenerating, so this just fans the same content out to
// every target account picked earlier.
function ReviewAndPublish({
  job,
  contentKind,
  initialCaption,
  accounts,
  targetAccountIds,
}: {
  job: GenerationJob;
  contentKind: string;
  initialCaption: string;
  accounts: SocialAccount[];
  targetAccountIds: string[];
}) {
  const { token } = useAuth();
  const imageUrl = job.output_payload?.image_url;
  const videoUrl = job.output_payload?.video_url;
  const [text, setText] = useState(job.output_payload?.text ?? initialCaption);
  const [scheduledFor, setScheduledFor] = useState("");
  const [posts, setPosts] = useState<Post[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [publishing, setPublishing] = useState(false);

  const targetAccounts = accounts.filter((a) => targetAccountIds.includes(a.id));

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
          generation_job_id: job.id,
          scheduled_for: scheduledFor ? new Date(scheduledFor).toISOString() : null,
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
        <img src={imageUrl} alt="Сгенерированное изображение" style={{ maxWidth: "100%", borderRadius: 8 }} />
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
      <button type="submit" disabled={publishing}>
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

const CONTENT_TYPE_LABELS: Record<GenerationContentType, string> = {
  text: "Текст",
  image: "Изображение",
  video: "Видео",
};

// .doc (legacy binary Word) is deliberately excluded -- the backend
// parser (python-docx) only reads the OOXML .docx format.
const ATTACHMENT_ACCEPT = ".txt,.md,.pdf,.docx,image/*,video/*,audio/*";

const ATTACHMENT_TYPE_LABELS: Record<Attachment["attachment_type"], string> = {
  image: "фото",
  video: "видео",
  audio: "аудио",
  document: "документ",
};

// Mirrors app/content_pipeline/attachments.py's validate_attachment_set
// (CIN-107): up to 5 attachments total, any mix of document/image, but
// video and audio are capped at 1 each below that total.
const MAX_TOTAL_ATTACHMENTS = 5;
const PER_TYPE_ATTACHMENT_CAPS: Partial<Record<Attachment["attachment_type"], number>> = {
  video: 1,
  audio: 1,
};

type NamedAttachment = Attachment & { name: string };

function attachmentCapReached(attachments: NamedAttachment[], attachmentType: Attachment["attachment_type"]): boolean {
  if (attachments.length >= MAX_TOTAL_ATTACHMENTS) return true;
  const perTypeCap = PER_TYPE_ATTACHMENT_CAPS[attachmentType];
  if (perTypeCap === undefined) return false;
  return attachments.filter((a) => a.attachment_type === attachmentType).length >= perTypeCap;
}

function GenerateForm() {
  const { token } = useAuth();
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [accountsLoaded, setAccountsLoaded] = useState(false);
  const [targetAccountIds, setTargetAccountIds] = useState<string[]>([]);
  const [topic, setTopic] = useState("");
  const [contentType, setContentType] = useState<GenerationContentType>("text");
  const [contentKind, setContentKind] = useState("post");
  const [brandGuide, setBrandGuide] = useState("");
  const [attachments, setAttachments] = useState<NamedAttachment[]>([]);
  const [uploadingAttachment, setUploadingAttachment] = useState(false);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    api.get<SocialAccount[]>("/social-accounts", token).then((list) => {
      setAccounts(list);
      setAccountsLoaded(true);
    });
  }, [token]);

  function toggleTargetAccount(accountId: string, checked: boolean) {
    const next = checked ? [...targetAccountIds, accountId] : targetAccountIds.filter((id) => id !== accountId);
    setTargetAccountIds(next);

    const nextPlatforms = platformsFor(next, accounts);
    const allowedTypes = allowedContentTypesFor(nextPlatforms);
    const nextContentType = allowedTypes.length > 0 && !allowedTypes.includes(contentType) ? allowedTypes[0] : contentType;
    if (nextContentType !== contentType) setContentType(nextContentType);

    const allowedKinds = allowedContentKindsFor(nextPlatforms, nextContentType);
    if (!allowedKinds.includes(contentKind)) setContentKind(allowedKinds[0] ?? "post");
  }

  const selectedPlatforms = platformsFor(targetAccountIds, accounts);
  const allowedContentTypes = allowedContentTypesFor(selectedPlatforms);
  const allowedContentKinds = allowedContentKindsFor(selectedPlatforms, contentType);

  async function handleAttachmentChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = ""; // allow re-selecting the same file later
    if (!file) return;
    setAttachmentError(null);
    if (attachments.length >= MAX_TOTAL_ATTACHMENTS) {
      setAttachmentError(`Максимум ${MAX_TOTAL_ATTACHMENTS} вложений за генерацию`);
      return;
    }
    setUploadingAttachment(true);
    try {
      const uploaded = await api.upload<Attachment>("/content/attachment", file, token);
      if (attachmentCapReached(attachments, uploaded.attachment_type)) {
        setAttachmentError(
          `Достигнут лимит для типа «${ATTACHMENT_TYPE_LABELS[uploaded.attachment_type]}»`
        );
        return;
      }
      setAttachments((prev) => [...prev, { ...uploaded, name: file.name }]);
    } catch (err) {
      setAttachmentError(err instanceof ApiError ? err.message : "Не удалось загрузить файл");
    } finally {
      setUploadingAttachment(false);
    }
  }

  function removeAttachment(index: number) {
    setAttachments((prev) => prev.filter((_, i) => i !== index));
    setAttachmentError(null);
  }

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  function pollJob(jobId: string) {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const updated = await api.get<GenerationJob>(`/content/${jobId}`, token);
        setJob(updated);
        if (TERMINAL_STATUSES.has(updated.status) && pollRef.current) {
          clearInterval(pollRef.current);
        }
      } catch {
        if (pollRef.current) clearInterval(pollRef.current);
      }
    }, POLL_INTERVAL_MS);
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setJob(null);
    setSubmitting(true);
    try {
      const created = await api.post<GenerationJob>(
        "/content/generate",
        {
          topic,
          target_account_ids: targetAccountIds,
          content_type: contentType,
          content_kind: contentKind,
          brand_guide: brandGuide || null,
          attachments: attachments.map((a) => ({ url: a.url, attachment_type: a.attachment_type })),
        },
        token
      );
      setJob(created);
      if (!TERMINAL_STATUSES.has(created.status)) pollJob(created.id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        setError("Лимит генераций по тарифу исчерпан. Обновите тариф на странице «Тариф».");
      } else {
        setError(err instanceof ApiError ? err.message : "Не удалось запустить генерацию");
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (accountsLoaded && accounts.length === 0) {
    return (
      <>
        <div className="page-header">
          <div>
            <h1>Генерация контента</h1>
            <p className="muted">Опишите задачу — Cindra подготовит черновик под выбранный канал</p>
          </div>
        </div>
        <p className="muted">Чтобы начать генерацию, сначала подключите соцсеть на странице «Соцсети».</p>
      </>
    );
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Генерация контента</h1>
          <p className="muted">Опишите задачу — Cindra подготовит черновик под выбранный канал</p>
        </div>
      </div>
      <form onSubmit={handleSubmit} className="card">
        <fieldset className="chip-group">
          <legend>Куда опубликовать</legend>
          {accounts.map((a) => (
            <label key={a.id}>
              <input
                type="checkbox"
                checked={targetAccountIds.includes(a.id)}
                onChange={(e) => toggleTargetAccount(a.id, e.target.checked)}
              />
              {a.platform} — {a.display_name ?? a.external_account_id}
            </label>
          ))}
        </fieldset>
        <label>
          Запрос
          <textarea
            required
            rows={6}
            maxLength={5000}
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="например, осенняя коллекция кофе"
          />
        </label>
        <label>
          Формат
          <select
            value={contentType}
            disabled={targetAccountIds.length === 0}
            onChange={(e) => {
              const nextContentType = e.target.value as GenerationContentType;
              setContentType(nextContentType);
              const available = allowedContentKindsFor(selectedPlatforms, nextContentType);
              if (!available.includes(contentKind)) setContentKind(available[0] ?? "post");
            }}
          >
            {allowedContentTypes.map((value) => (
              <option key={value} value={value}>
                {CONTENT_TYPE_LABELS[value]}
              </option>
            ))}
          </select>
        </label>
        <label>
          Тип контента
          <select
            value={contentKind}
            disabled={targetAccountIds.length === 0}
            onChange={(e) => setContentKind(e.target.value)}
          >
            {allowedContentKinds.map((value) => (
              <option key={value} value={value}>
                {CONTENT_KIND_LABELS[value] ?? value}
              </option>
            ))}
          </select>
        </label>
        <label>
          Бренд-гайд (необязательно)
          <textarea
            rows={3}
            value={brandGuide}
            onChange={(e) => setBrandGuide(e.target.value)}
            placeholder="тон и стиль, которых нужно придерживаться"
          />
        </label>
        <label>
          Прикрепить файлы (необязательно, до {MAX_TOTAL_ATTACHMENTS}: видео и аудио — не больше 1 каждого)
          <input
            type="file"
            accept={ATTACHMENT_ACCEPT}
            onChange={handleAttachmentChange}
            disabled={uploadingAttachment || attachments.length >= MAX_TOTAL_ATTACHMENTS}
          />
        </label>
        {uploadingAttachment && <p className="muted">Загружаем файл…</p>}
        {attachmentError && <p className="error">{attachmentError}</p>}
        {attachments.length > 0 && (
          <ul>
            {attachments.map((a, index) => (
              <li key={`${a.url}-${index}`} className="muted">
                {a.name} ({ATTACHMENT_TYPE_LABELS[a.attachment_type]}){" "}
                <button type="button" className="secondary" onClick={() => removeAttachment(index)}>
                  Убрать
                </button>
              </li>
            ))}
          </ul>
        )}
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={submitting || uploadingAttachment || targetAccountIds.length === 0}>
          {submitting ? "Запускаем…" : "Сгенерировать"}
        </button>
      </form>

      {job && (
        <div className="card" style={{ marginTop: 24 }}>
          <p>
            Статус генерации: <span className={`badge ${job.status}`}>{job.status}</span>
          </p>
          {job.status === "completed" &&
            (job.output_payload?.text || job.output_payload?.image_url || job.output_payload?.video_url) && (
              <ReviewAndPublish
                job={job}
                contentKind={contentKind}
                initialCaption={topic}
                accounts={accounts}
                targetAccountIds={targetAccountIds}
              />
            )}
          {job.status === "failed" && <p className="error">{job.error_message}</p>}
          {job.status === "flagged" && (
            <p className="error">Отклонено модерацией: {job.error_message}</p>
          )}
          {(job.status === "queued" || job.status === "processing") && (
            <p className="muted">Генерируем…</p>
          )}
        </div>
      )}
    </>
  );
}

export default function GeneratePage() {
  return (
    <RequireAuth>
      <GenerateForm />
    </RequireAuth>
  );
}
