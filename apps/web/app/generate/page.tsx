"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { GenerationJob, Post, SocialAccount, SocialPlatform } from "@/lib/types";

import { RequireAuth } from "../components/RequireAuth";

const TERMINAL_STATUSES = new Set(["completed", "failed", "flagged"]);
const POLL_INTERVAL_MS = 2000;

// The review/edit-before-publish step (CIN-38): once generation
// completes, the raw text becomes an editable draft here rather than
// a separate screen -- reviewing what you just generated is part of
// the same flow, not a different destination.
function ReviewAndPublish({ job }: { job: GenerationJob }) {
  const { token } = useAuth();
  const [text, setText] = useState(job.output_payload?.text ?? "");
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
      <label>
        Текст (можно отредактировать перед публикацией)
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

function GenerateForm() {
  const { token } = useAuth();
  const [topic, setTopic] = useState("");
  const [platform, setPlatform] = useState<SocialPlatform>("telegram");
  const [contentKind, setContentKind] = useState("post");
  const [brandGuide, setBrandGuide] = useState("");
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

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
        { topic, platform, content_kind: contentKind, brand_guide: brandGuide || null },
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
          Тема
          <input
            required
            maxLength={500}
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="например, осенняя коллекция кофе"
          />
        </label>
        <label>
          Платформа
          <select value={platform} onChange={(e) => setPlatform(e.target.value as SocialPlatform)}>
            <option value="telegram">Telegram</option>
            <option value="instagram">Instagram</option>
          </select>
        </label>
        <label>
          Тип контента
          <select value={contentKind} onChange={(e) => setContentKind(e.target.value)}>
            <option value="post">Пост</option>
            <option value="story">Сторис</option>
            <option value="video_script">Сценарий видео</option>
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
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={submitting}>
          {submitting ? "Запускаем…" : "Сгенерировать"}
        </button>
      </form>

      {job && (
        <div className="card" style={{ marginTop: 24 }}>
          <p>
            Статус генерации: <span className={`badge ${job.status}`}>{job.status}</span>
          </p>
          {job.status === "completed" && job.output_payload?.text && (
            <ReviewAndPublish job={job} />
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
