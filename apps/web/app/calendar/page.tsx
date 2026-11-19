"use client";

import { ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type DragEvent, type FormEvent } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import {
  addDays,
  minLocalInput,
  sameDay,
  startOfDay,
  startOfWeek,
  timezoneLabel,
  toLocalInputValue,
} from "@/lib/datetime";
import type { Page, Post, SocialAccount } from "@/lib/types";

import { Dialog } from "../components/Dialog";
import { RequireAuth } from "../components/RequireAuth";
import { ChipSelect, platformLabel } from "../components/Studio";

type Mode = "week" | "month" | "list";

const MODE_OPTIONS: { value: Mode; label: string }[] = [
  { value: "week", label: "Неделя" },
  { value: "month", label: "Месяц" },
  { value: "list", label: "Список" },
];

const STATUS_LABELS: Record<string, string> = {
  scheduled: "Запланирована",
  publishing: "Публикуется",
  published: "Опубликована",
  failed: "Ошибка",
};

const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU");
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

function shortText(text: string, max = 40): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

// Текст публикации обрезан в списке до 80 символов -- иконка рядом
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
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
      )}
    </span>
  );
}

// Small thumbnail so a photo/video post is recognizable at a glance
// without blowing up row height -- text is still shown for every
// post regardless (CIN-116), this is purely additive.
function PostMedia({ post, large = false }: { post: Post; large?: boolean }) {
  const className = large ? "post-media" : "thumb";
  if (post.image_url) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={post.image_url} alt="" className={className} />;
  }
  if (post.video_url) {
    return <video src={post.video_url} muted controls={large} className={className} />;
  }
  return null;
}

// Планирование публикации прямо здесь, без обязательного прохождения
// генерации контента (CIN-76) -- POST /posts уже поддерживает пост
// без generation_job_id, не хватало только формы.
function CreatePostForm({ initialWhen, onCreated }: { initialWhen: string; onCreated: () => void }) {
  const { token } = useAuth();
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [accountId, setAccountId] = useState("");
  const [text, setText] = useState("");
  const [scheduledFor, setScheduledFor] = useState(initialWhen);
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
    <form onSubmit={handleSubmit} className="dialog-form">
      <label className="field-block">
        <span className="field-label">Текст</span>
        <textarea
          rows={4}
          required
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="текст публикации"
        />
      </label>
      <label className="field-block">
        <span className="field-label">Куда опубликовать</span>
        <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {platformLabel(a.platform)} — {a.display_name ?? a.external_account_id}
            </option>
          ))}
        </select>
      </label>
      <label className="field-block">
        <span className="field-label">
          Когда ({timezoneLabel()}; пусто — опубликуем сразу)
        </span>
        <input
          type="datetime-local"
          min={minLocalInput()}
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

function PostActions({ post, onChanged }: { post: Post; onChanged: () => void }) {
  const { token } = useAuth();
  const [rescheduling, setRescheduling] = useState(false);
  const [newScheduledFor, setNewScheduledFor] = useState(() => toLocalInputValue(new Date(post.scheduled_for)));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (post.status !== "scheduled") return null;

  async function handleReschedule() {
    setError(null);
    setBusy(true);
    try {
      await api.patch(`/posts/${post.id}`, { scheduled_for: new Date(newScheduledFor).toISOString() }, token);
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
      <div className="list-row-actions">
        <input
          type="datetime-local"
          aria-label="Новое время"
          min={minLocalInput()}
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

function PostDetail({ post, onChanged }: { post: Post; onChanged: () => void }) {
  return (
    <div className="post-detail">
      <p className="muted">
        {platformLabel(post.platform)} · {post.account_label} · {formatDate(post.scheduled_for)}{" "}
        <span className={`badge ${post.status}`}>{STATUS_LABELS[post.status] ?? post.status}</span>
      </p>
      <PostMedia post={post} large />
      <p className="post-detail-text">{post.text}</p>
      {post.status === "failed" && post.error_message && <p className="error">{post.error_message}</p>}
      <PostActions post={post} onChanged={onChanged} />
    </div>
  );
}

// One post inside a day cell. Scheduled ones can be dragged to another
// day (same time of day) -- the quickest way to move a plan around.
function CalendarPost({ post, onOpen }: { post: Post; onOpen: (post: Post) => void }) {
  const draggable = post.status === "scheduled";
  return (
    <button
      type="button"
      className={`cal-post ${post.status}`}
      draggable={draggable}
      onDragStart={(event: DragEvent) => {
        event.dataTransfer.setData("text/post-id", post.id);
        event.dataTransfer.effectAllowed = "move";
      }}
      onClick={() => onOpen(post)}
      title={`${platformLabel(post.platform)}: ${post.text}`}
    >
      <b>{formatTime(post.scheduled_for)}</b> {shortText(post.text, 28)}
    </button>
  );
}

function monthGridStart(anchor: Date): Date {
  return startOfWeek(new Date(anchor.getFullYear(), anchor.getMonth(), 1));
}

function CalendarBoard({ mode }: { mode: "week" | "month" }) {
  const { token } = useAuth();
  const [anchor, setAnchor] = useState(() => startOfDay(new Date()));
  const [posts, setPosts] = useState<Post[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [opened, setOpened] = useState<Post | null>(null);
  const [creatingFor, setCreatingFor] = useState<string | null>(null);

  const days = useMemo(() => {
    if (mode === "week") {
      const start = startOfWeek(anchor);
      return Array.from({ length: 7 }, (_, i) => addDays(start, i));
    }
    const start = monthGridStart(anchor);
    const monthEnd = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0);
    const weeks = Math.ceil(((startOfWeek(monthEnd).getTime() - start.getTime()) / 86400000 + 7) / 7);
    return Array.from({ length: weeks * 7 }, (_, i) => addDays(start, i));
  }, [anchor, mode]);

  const rangeStart = days[0];
  const rangeEnd = addDays(days[days.length - 1], 1);

  const load = useCallback(() => {
    const params = new URLSearchParams({
      from: rangeStart.toISOString(),
      to: rangeEnd.toISOString(),
      limit: "100",
    });
    api
      .get<Page<Post>>(`/posts?${params}`, token)
      .then((page) => {
        setPosts(page.items);
        setTotal(page.total);
        setError(null);
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : "Не удалось загрузить"));
  }, [token, rangeStart, rangeEnd]);

  useEffect(load, [load]);

  const byDay = useMemo(() => {
    const map = new Map<string, Post[]>();
    for (const post of posts ?? []) {
      const key = startOfDay(new Date(post.scheduled_for)).toDateString();
      map.set(key, [...(map.get(key) ?? []), post].sort((a, b) => a.scheduled_for.localeCompare(b.scheduled_for)));
    }
    return map;
  }, [posts]);

  function step(direction: -1 | 1) {
    setAnchor((current) =>
      mode === "week"
        ? addDays(current, 7 * direction)
        : new Date(current.getFullYear(), current.getMonth() + direction, 1)
    );
  }

  async function moveTo(postId: string, day: Date) {
    const post = posts?.find((p) => p.id === postId);
    if (!post || post.status !== "scheduled") return;
    const original = new Date(post.scheduled_for);
    const target = new Date(day.getFullYear(), day.getMonth(), day.getDate(), original.getHours(), original.getMinutes());
    if (target.getTime() === original.getTime()) return;
    if (target.getTime() < Date.now()) {
      setError("Нельзя перенести на момент в прошлом — выберите другой день.");
      return;
    }
    setError(null);
    try {
      await api.patch(`/posts/${postId}`, { scheduled_for: target.toISOString() }, token);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось перенести");
    }
  }

  const today = new Date();
  const title =
    mode === "week"
      ? `${days[0].toLocaleDateString("ru-RU", { day: "numeric", month: "long" })} — ${days[6].toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" })}`
      : anchor.toLocaleDateString("ru-RU", { month: "long", year: "numeric" });

  return (
    <>
      <div className="cal-toolbar">
        <div className="cal-nav">
          <button type="button" className="secondary icon-button" onClick={() => step(-1)} aria-label="Назад">
            <ChevronLeft size={16} />
          </button>
          <button type="button" className="secondary" onClick={() => setAnchor(startOfDay(new Date()))}>
            Сегодня
          </button>
          <button type="button" className="secondary icon-button" onClick={() => step(1)} aria-label="Вперёд">
            <ChevronRight size={16} />
          </button>
        </div>
        <h2 className="cal-title">{title}</h2>
        <span className="muted hint">время вашего устройства, {timezoneLabel()}</span>
      </div>
      {error && <p className="error">{error}</p>}
      {posts === null && !error && <div className="skeleton" aria-busy="true" />}
      {posts !== null && (
        <div className={`calendar ${mode}`} role="grid" aria-label={title}>
          {WEEKDAYS.map((name) => (
            <div key={name} className="cal-head muted" role="columnheader">
              {name}
            </div>
          ))}
          {days.map((day) => {
            const items = byDay.get(day.toDateString()) ?? [];
            const outside = mode === "month" && day.getMonth() !== anchor.getMonth();
            const past = startOfDay(day).getTime() < startOfDay(today).getTime();
            const shown = mode === "month" ? items.slice(0, 3) : items;
            return (
              <div
                key={day.toISOString()}
                role="gridcell"
                className={`day${sameDay(day, today) ? " today" : ""}${outside ? " outside" : ""}${past ? " past" : ""}`}
                onDragOver={(event) => {
                  if (!past) event.preventDefault();
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  const id = event.dataTransfer.getData("text/post-id");
                  if (id) moveTo(id, day);
                }}
              >
                <div className="day-top">
                  <b>
                    <span className="day-weekday">{WEEKDAYS[(day.getDay() + 6) % 7]}, </span>
                    {day.getDate()}
                  </b>
                  {!past && (
                    <button
                      type="button"
                      className="ghost day-add"
                      aria-label={`Запланировать на ${day.toLocaleDateString("ru-RU", { day: "numeric", month: "long" })}`}
                      onClick={() => setCreatingFor(toLocalInputValue(new Date(day.getFullYear(), day.getMonth(), day.getDate(), 10, 0)))}
                    >
                      <Plus size={14} />
                    </button>
                  )}
                </div>
                {shown.map((post) => (
                  <CalendarPost key={post.id} post={post} onOpen={setOpened} />
                ))}
                {items.length > shown.length && (
                  <button type="button" className="ghost day-more" onClick={() => setOpened(items[shown.length])}>
                    ещё {items.length - shown.length}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
      {posts !== null && total > posts.length && (
        <p className="muted hint">Показаны первые {posts.length} из {total} публикаций периода.</p>
      )}
      {posts !== null && posts.length === 0 && !error && (
        <p className="muted hint">В этом периоде публикаций нет. Нажмите «+» в любом дне, чтобы запланировать.</p>
      )}

      <Dialog open={opened !== null} title="Публикация" onClose={() => setOpened(null)}>
        {opened && (
          <PostDetail
            post={opened}
            onChanged={() => {
              setOpened(null);
              load();
            }}
          />
        )}
      </Dialog>
      <Dialog open={creatingFor !== null} title="Запланировать публикацию" onClose={() => setCreatingFor(null)}>
        {creatingFor !== null && (
          <CreatePostForm
            initialWhen={creatingFor}
            onCreated={() => {
              setCreatingFor(null);
              load();
            }}
          />
        )}
      </Dialog>
    </>
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
      {error && <p className="error">{error}</p>}
      {posts === null && !error && <div className="skeleton" aria-busy="true" />}
      {posts && posts.length === 0 && <p className="muted">Публикаций пока нет.</p>}
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
                  <span>{platformLabel(post.platform)}</span>
                  <span>·</span>
                  <span>{post.account_label}</span>
                  <span>·</span>
                  <span>{formatDate(post.scheduled_for)}</span>
                </p>
              </div>
              <div className="list-row-side">
                <span className={`badge ${post.status}`}>{STATUS_LABELS[post.status] ?? post.status}</span>
                {post.status === "failed" && post.error_message && <div className="muted">{post.error_message}</div>}
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
    </>
  );
}

function CalendarPage() {
  const [mode, setMode] = useState<Mode>("week");
  const [creating, setCreating] = useState(false);
  const [version, setVersion] = useState(0);

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Всё идёт по плану</h1>
          <p className="muted">Что и когда уйдёт в соцсети. Перетащите запланированную публикацию на другой день, чтобы перенести её.</p>
        </div>
        <div className="page-header-actions">
          <button type="button" onClick={() => setCreating(true)}>
            <Plus size={16} aria-hidden="true" /> Запланировать
          </button>
        </div>
      </div>
      <ChipSelect label="Вид" value={mode} options={MODE_OPTIONS} onChange={setMode} />
      <div className="section">
        {mode === "list" ? <CalendarList key={version} /> : <CalendarBoard key={`${mode}-${version}`} mode={mode} />}
      </div>
      <Dialog open={creating} title="Запланировать публикацию" onClose={() => setCreating(false)}>
        {creating && (
          <CreatePostForm
            initialWhen=""
            onCreated={() => {
              setCreating(false);
              setVersion((v) => v + 1);
            }}
          />
        )}
      </Dialog>
    </>
  );
}

export default function CalendarRoute() {
  return (
    <RequireAuth>
      <CalendarPage />
    </RequireAuth>
  );
}
