"use client";

import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
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
// across a long-lived page instead of freezing at page load.
function minDatetimeLocal(): string {
  return new Date().toISOString().slice(0, 16);
}

// The review/edit-before-publish step (CIN-38): once generation
// completes, the raw text becomes an editable draft here rather than
// a separate screen -- reviewing what you just generated is part of
// the same flow, not a different destination.
function ReviewAndPublish({
  job,
  contentKind,
  initialCaption,
}: {
  job: GenerationJob;
  contentKind: string;
  initialCaption: string;
}) {
  const { token } = useAuth();
  const imageUrl = job.output_payload?.image_url;
  const videoUrl = job.output_payload?.video_url;
  const [text, setText] = useState(job.output_payload?.text ?? initialCaption);
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [accountId, setAccountId] = useState("");
  const [scheduledFor, setScheduledFor] = useState("");
  const [post, setPost] = useState<Post | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [publishing, setPublishing] = useState(false);

  useEffect(() => {
    api.get<SocialAccount[]>("/social-accounts", token).then((list) => {
      setAccounts(list);
      if (list.length > 0) setAccountId(list[0].id);
    });
  }, [token]);

  async function handlePublish(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setPublishing(true);
    try {
      const created = await api.post<Post>(
        "/posts",
        {
          social_account_id: accountId,
          text,
          image_url: imageUrl ?? null,
          video_url: videoUrl ?? null,
          content_kind: contentKind,
          generation_job_id: job.id,
          scheduled_for: scheduledFor ? new Date(scheduledFor).toISOString() : null,
        },
        token
      );
      setPost(created);
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        setError("Лимит публикаций по тарифу исчерпан.");
      } else {
        setError(err instanceof ApiError ? err.message : "Не удалось опубликовать");
      }
    } finally {
      setPublishing(false);
    }
  }

  if (accounts.length === 0) {
    return (
      <p className="muted">
        Чтобы опубликовать, сначала подключите соцсеть на странице «Соцсети».
      </p>
    );
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
      <label>
        Куда опубликовать
        <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.platform} — {a.display_name ?? a.external_account_id}
            </option>
          ))}
        </select>
      </label>
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
      {post && (
        <p>
          Статус публикации: <span className={`badge ${post.status}`}>{post.status}</span>
          {post.status === "failed" && post.error_message && (
            <span className="error"> — {post.error_message}</span>
          )}
        </p>
      )}
    </form>
  );
}

// "Сторис" сегодня реально публикуется только для Instagram (CIN-74)
// -- у Telegram/Facebook нет своего эквивалента в нашем пайплайне, не
// показываем эту опцию там, чтобы не обещать то, чего нет.
const CONTENT_KIND_OPTIONS: Record<SocialPlatform, { value: string; label: string }[]> = {
  telegram: [
    { value: "post", label: "Пост" },
    { value: "video_script", label: "Сценарий видео" },
  ],
  instagram: [
    { value: "post", label: "Пост" },
    { value: "story", label: "Сторис" },
    { value: "video_script", label: "Сценарий видео" },
  ],
  facebook: [
    { value: "post", label: "Пост" },
    { value: "video_script", label: "Сценарий видео" },
  ],
};

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

// "Сценарий видео" -- это content_kind для ТЕКСТА (сценарий, который
// человек потом сам снимает), не имеет смысла как приложение к
// реально сгенерированному изображению/видео (CIN-93).
function contentKindOptionsFor(platform: SocialPlatform, contentType: GenerationContentType) {
  const options = CONTENT_KIND_OPTIONS[platform];
  return contentType === "text" ? options : options.filter((o) => o.value !== "video_script");
}

function GenerateForm() {
  const { token } = useAuth();
  const [topic, setTopic] = useState("");
  const [platform, setPlatform] = useState<SocialPlatform>("telegram");
  const [contentType, setContentType] = useState<GenerationContentType>("text");
  const [contentKind, setContentKind] = useState("post");
  const [brandGuide, setBrandGuide] = useState("");
  const [attachment, setAttachment] = useState<Attachment | null>(null);
  const [attachmentName, setAttachmentName] = useState("");
  const [uploadingAttachment, setUploadingAttachment] = useState(false);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function handleAttachmentChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = ""; // allow re-selecting the same file later
    if (!file) return;
    setAttachmentError(null);
    setUploadingAttachment(true);
    try {
      const uploaded = await api.upload<Attachment>("/content/attachment", file, token);
      setAttachment(uploaded);
      setAttachmentName(file.name);
    } catch (err) {
      setAttachmentError(err instanceof ApiError ? err.message : "Не удалось загрузить файл");
    } finally {
      setUploadingAttachment(false);
    }
  }

  function removeAttachment() {
    setAttachment(null);
    setAttachmentName("");
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
          platform,
          content_type: contentType,
          content_kind: contentKind,
          brand_guide: brandGuide || null,
          attachment_url: attachment?.url ?? null,
          attachment_type: attachment?.attachment_type ?? null,
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

  return (
    <>
      <h1>Генерация контента</h1>
      <form onSubmit={handleSubmit}>
        <label>
          Запрос
          <textarea
            required
            rows={4}
            maxLength={500}
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="например, осенняя коллекция кофе"
          />
        </label>
        <label>
          Платформа
          <select
            value={platform}
            onChange={(e) => {
              const nextPlatform = e.target.value as SocialPlatform;
              setPlatform(nextPlatform);
              const available = contentKindOptionsFor(nextPlatform, contentType).map((o) => o.value);
              if (!available.includes(contentKind)) setContentKind("post");
            }}
          >
            <option value="telegram">Telegram</option>
            <option value="instagram">Instagram</option>
            <option value="facebook">Facebook</option>
          </select>
        </label>
        <label>
          Формат
          <select
            value={contentType}
            onChange={(e) => {
              const nextContentType = e.target.value as GenerationContentType;
              setContentType(nextContentType);
              const available = contentKindOptionsFor(platform, nextContentType).map((o) => o.value);
              if (!available.includes(contentKind)) setContentKind("post");
            }}
          >
            {(Object.keys(CONTENT_TYPE_LABELS) as GenerationContentType[]).map((value) => (
              <option key={value} value={value}>
                {CONTENT_TYPE_LABELS[value]}
              </option>
            ))}
          </select>
        </label>
        <label>
          Тип контента
          <select value={contentKind} onChange={(e) => setContentKind(e.target.value)}>
            {contentKindOptionsFor(platform, contentType).map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
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
          Прикрепить файл (необязательно)
          <input type="file" accept={ATTACHMENT_ACCEPT} onChange={handleAttachmentChange} />
        </label>
        {uploadingAttachment && <p className="muted">Загружаем файл…</p>}
        {attachmentError && <p className="error">{attachmentError}</p>}
        {attachment && !uploadingAttachment && (
          <p className="muted">
            Прикреплено: {attachmentName} ({ATTACHMENT_TYPE_LABELS[attachment.attachment_type]}){" "}
            <button type="button" className="secondary" onClick={removeAttachment}>
              Убрать
            </button>
          </p>
        )}
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={submitting || uploadingAttachment}>
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
              <ReviewAndPublish job={job} contentKind={contentKind} initialCaption={topic} />
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
