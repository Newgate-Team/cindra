"use client";

import { useEffect, useState, type FormEvent } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { Page, Post, SocialAccount } from "@/lib/types";

import { ChevronDownIcon, ChevronUpIcon } from "../components/icons";
import { RequireAuth } from "../components/RequireAuth";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU");
}

const PLATFORM_LABELS: Record<Post["platform"], string> = {
  telegram: "Telegram",
  instagram: "Instagram",
  facebook: "Facebook",
  tiktok: "TikTok",
};

// Текст публикации обрезан в таблице до 80 символов -- иконка рядом
// разворачивает/сворачивает полный текст прямо в строке (CIN-83).
function PostText({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const isTruncated = text.length > 80;
  const preview = isTruncated ? `${text.slice(0, 80)}…` : text;

  return (
    <span>
      {expanded ? text : preview}
      {isTruncated && (
        <button
          type="button"
          className="secondary icon-button"
          onClick={() => setExpanded((v) => !v)}
          aria-label={expanded ? "Свернуть текст публикации" : "Показать полный текст публикации"}
          title={expanded ? "Свернуть текст публикации" : "Показать полный текст публикации"}
        >
          {expanded ? <ChevronUpIcon size={14} /> : <ChevronDownIcon size={14} />}
        </button>
      )}
    </span>
  );
}

// Small thumbnail so a photo/video post is recognizable at a glance
// without blowing up row height -- text is still shown for every
// post regardless (CIN-116), this is purely additive.
function PostMedia({ post }: { post: Post }) {
  if (post.image_url) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={post.image_url} alt="" style={{ width: 60, height: 60, objectFit: "cover", borderRadius: 4 }} />;
  }
  if (post.video_url) {
    return (
      <video
        src={post.video_url}
        muted
        style={{ width: 60, height: 60, objectFit: "cover", borderRadius: 4 }}
      />
    );
  }
  return null;
}

// datetime-local's `min` needs "yyyy-MM-ddTHH:mm" -- called fresh on
// every render rather than a module-level constant, so it stays "now"
// across a long-lived page instead of freezing at page load.
function minDatetimeLocal(): string {
  return new Date().toISOString().slice(0, 16);
}

// Планирование публикации прямо здесь, без обязательного прохождения
// генерации контента (CIN-76) -- POST /posts уже поддерживает пост
// без generation_job_id, не хватало только формы.
function CreatePostForm({ onCreated }: { onCreated: () => void }) {
  const { token } = useAuth();
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [accountId, setAccountId] = useState("");
  const [text, setText] = useState("");
  const [scheduledFor, setScheduledFor] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api.get<SocialAccount[]>("/social-accounts", token).then((list) => {
      // This compact manual form has no media uploader. TikTok only
      // accepts a video, so it belongs in the generate/review flow.
      const manualAccounts = list.filter((account) => account.platform !== "tiktok");
      setAccounts(manualAccounts);
      if (manualAccounts.length > 0) setAccountId(manualAccounts[0].id);
    });
  }, [token]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await api.post<Post[]>(
        "/posts",
        {
          social_account_ids: [accountId],
          text,
          scheduled_for: scheduledFor ? new Date(scheduledFor).toISOString() : null,
        },
        token
      );
      setText("");
      setScheduledFor("");
      onCreated();
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        setError("Лимит публикаций по тарифу исчерпан.");
      } else {
        setError(err instanceof ApiError ? err.message : "Не удалось запланировать");
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (accounts.length === 0) {
    return (
      <p className="muted">
        Чтобы запланировать публикацию, сначала подключите соцсеть на странице «Соцсети».
      </p>
    );
  }

  return (
    <form onSubmit={handleSubmit}>
      <label>
        Текст
        <textarea
          rows={4}
          required
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="текст публикации"
        />
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
      <button type="submit" disabled={submitting}>
        {submitting ? "Сохраняем…" : scheduledFor ? "Запланировать" : "Опубликовать сейчас"}
      </button>
    </form>
  );
}

// datetime-local нужен формат "yyyy-MM-ddTHH:mm" без секунд/зоны --
// toISOString даёт "...ss.sssZ", обрезаем до минут.
function toDatetimeLocalValue(iso: string): string {
  return new Date(iso).toISOString().slice(0, 16);
}

function StatusBadge({ post }: { post: Post }) {
  return (
    <>
      <span className={`badge ${post.status}`}>{post.status}</span>
      {post.status === "failed" && post.error_message && (
        <div className="muted">{post.error_message}</div>
      )}
    </>
  );
}

function PostActions({ post, onChanged }: { post: Post; onChanged: () => void }) {
  const { token } = useAuth();
  const [rescheduling, setRescheduling] = useState(false);
  const [newScheduledFor, setNewScheduledFor] = useState(() =>
    toDatetimeLocalValue(post.scheduled_for)
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (post.status !== "scheduled") return null;

  async function handleReschedule() {
    setError(null);
    setBusy(true);
    try {
      await api.patch(
        `/posts/${post.id}`,
        { scheduled_for: new Date(newScheduledFor).toISOString() },
        token
      );
      setRescheduling(false);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось перенести");
    } finally {
      setBusy(false);
    }
  }

  async function handleCancel() {
    setError(null);
    setBusy(true);
    try {
      await api.delete(`/posts/${post.id}`, token);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось отменить");
      setBusy(false);
    }
  }

  if (rescheduling) {
    return (
      <div>
        <input
          type="datetime-local"
          min={minDatetimeLocal()}
          value={newScheduledFor}
          onChange={(e) => setNewScheduledFor(e.target.value)}
        />
        <button type="button" disabled={busy} onClick={handleReschedule}>
          Сохранить
        </button>
        <button type="button" className="secondary" onClick={() => setRescheduling(false)}>
          Отмена
        </button>
        {error && <p className="error">{error}</p>}
      </div>
    );
  }

  return (
    <div className="list-row-actions">
      <button type="button" onClick={() => setRescheduling(true)}>
        Перенести
      </button>
      <button type="button" className="secondary" disabled={busy} onClick={handleCancel}>
        Отменить
      </button>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

const PAGE_SIZE = 20;

function CalendarList() {
  const { token } = useAuth();
  const [page, setPage] = useState<Page<Post> | null>(null);
  const [pageIndex, setPageIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);

  function reload() {
    api
      .get<Page<Post>>(`/posts?limit=${PAGE_SIZE}&offset=${pageIndex * PAGE_SIZE}`, token)
      .then(setPage)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Не удалось загрузить"));
  }

  useEffect(reload, [token, pageIndex]);

  const posts = page?.items ?? null;
  const total = page?.total ?? 0;
  const rangeStart = total === 0 ? 0 : pageIndex * PAGE_SIZE + 1;
  const rangeEnd = Math.min(total, (pageIndex + 1) * PAGE_SIZE);
  const hasNextPage = rangeEnd < total;

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Календарь публикаций</h1>
          {posts !== null && (
            <p className="muted">{total > 0 ? `Всего публикаций: ${total}` : "Публикаций пока нет"}</p>
          )}
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {posts === null && !error && <p className="muted">Загрузка…</p>}
      {posts && posts.length > 0 && (
        <>
          {posts.map((post) => (
            <div key={post.id} className="card list-row">
              <div className="list-row-media">
                <PostMedia post={post} />
              </div>
              <div className="list-row-body">
                <PostText text={post.text} />
                <p className="muted list-row-meta">
                  <span>{PLATFORM_LABELS[post.platform]}</span>
                  <span>·</span>
                  <span>{post.account_label}</span>
                  <span>·</span>
                  <span>{formatDate(post.scheduled_for)}</span>
                </p>
              </div>
              <div className="list-row-side">
                <StatusBadge post={post} />
                <PostActions post={post} onChanged={reload} />
              </div>
            </div>
          ))}
          <div className="pagination">
            <button type="button" disabled={pageIndex === 0} onClick={() => setPageIndex((p) => p - 1)}>
              Назад
            </button>
            <span>
              показано {rangeStart}–{rangeEnd} из {total}
            </span>
            <button type="button" disabled={!hasNextPage} onClick={() => setPageIndex((p) => p + 1)}>
              Вперёд
            </button>
          </div>
        </>
      )}

      <h2>Запланировать публикацию</h2>
      <div className="card">
        <CreatePostForm
          onCreated={() => {
            // setPageIndex(0) only re-triggers the [token, pageIndex]
            // effect below when the index actually changes -- if we're
            // already on page 0, that effect won't fire, so reload()
            // has to be called directly to pick up the new post.
            if (pageIndex === 0) {
              reload();
            } else {
              setPageIndex(0);
            }
          }}
        />
      </div>
    </>
  );
}

export default function CalendarPage() {
  return (
    <RequireAuth>
      <CalendarList />
    </RequireAuth>
  );
}
