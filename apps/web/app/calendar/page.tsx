"use client";

import { useEffect, useState, type FormEvent } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { Post, SocialAccount } from "@/lib/types";

import { RequireAuth } from "../components/RequireAuth";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU");
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
      setAccounts(list);
      if (list.length > 0) setAccountId(list[0].id);
    });
  }, [token]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await api.post<Post>(
        "/posts",
        {
          social_account_id: accountId,
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
    <div>
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

function CalendarList() {
  const { token } = useAuth();
  const [posts, setPosts] = useState<Post[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  function reload() {
    api
      .get<Post[]>("/posts", token)
      .then(setPosts)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Не удалось загрузить"));
  }

  useEffect(reload, [token]);

  return (
    <>
      <h1>Календарь публикаций</h1>
      {error && <p className="error">{error}</p>}
      {posts === null && !error && <p className="muted">Загрузка…</p>}
      {posts?.length === 0 && <p className="muted">Публикаций пока нет.</p>}
      {posts && posts.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Когда</th>
              <th>Текст</th>
              <th>Статус</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {posts.map((post) => (
              <tr key={post.id}>
                <td>{formatDate(post.scheduled_for)}</td>
                <td>{post.text.length > 80 ? `${post.text.slice(0, 80)}…` : post.text}</td>
                <td>
                  <span className={`badge ${post.status}`}>{post.status}</span>
                  {post.status === "failed" && post.error_message && (
                    <div className="muted">{post.error_message}</div>
                  )}
                </td>
                <td>
                  <PostActions post={post} onChanged={reload} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2>Запланировать публикацию</h2>
      <CreatePostForm onCreated={reload} />
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
