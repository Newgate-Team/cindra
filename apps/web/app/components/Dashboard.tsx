"use client";

import {
  ArrowUp,
  CalendarDays,
  Check,
  CircleCheck,
  Clapperboard,
  Image as ImageIcon,
  Send,
  Sparkles,
  UserRound,
  Video,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState, type CSSProperties, type FormEvent } from "react";

import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { AnalyticsSummary, Page, Post, SocialAccount, SocialPlatform, UsageLimitStat } from "@/lib/types";

const PLATFORM_NAMES: Record<SocialPlatform, string> = {
  telegram: "Telegram",
  instagram: "Instagram",
  facebook: "Facebook",
  tiktok: "TikTok",
  youtube: "YouTube",
  linkedin: "LinkedIn",
  reddit: "Reddit",
  twitter: "X",
};

interface FormatChip {
  id: string;
  label: string;
  icon: LucideIcon;
  href: string;
  query?: string;
}

const FORMATS: FormatChip[] = [
  { id: "post", label: "Пост", icon: Sparkles, href: "/generate" },
  { id: "image", label: "Картинка", icon: ImageIcon, href: "/generate", query: "format=image" },
  { id: "video", label: "Видео", icon: Video, href: "/video" },
  { id: "avatar", label: "Аватар", icon: UserRound, href: "/video/avatar" },
  { id: "montage", label: "Монтаж", icon: Clapperboard, href: "/video/montage" },
];

// A ring's colour follows what it counts, the same colours the sections wear.
function usageColor(stat: UsageLimitStat): string {
  if (stat.content_type === "image") return "#ed4c88";
  if (stat.content_type === "video") return "#ef9651";
  if (stat.content_type === "text") return "#7c5cff";
  return "#14a995";
}

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 5) return "Доброй ночи";
  if (hour < 12) return "Доброе утро";
  if (hour < 18) return "Добрый день";
  return "Добрый вечер";
}

function whenLabel(iso: string): string {
  const date = new Date(iso);
  const time = date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const today = new Date();
  const tomorrow = new Date(today.getTime() + 24 * 3600 * 1000);
  const same = (a: Date, b: Date) => a.toDateString() === b.toDateString();
  if (same(date, today)) return `Сегодня · ${time}`;
  if (same(date, tomorrow)) return `Завтра · ${time}`;
  return `${date.toLocaleDateString("ru-RU", { day: "numeric", month: "short" })} · ${time}`;
}

function snippet(text: string, max = 70): string {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length > max ? `${clean.slice(0, max - 1)}…` : clean || "Без текста";
}

function Ring({ value, of, color }: { value: number; of: number; color: string }) {
  const percent = of > 0 ? Math.min(100, Math.round((value / of) * 100)) : 0;
  // Amber from 80% and red at the limit: the colour warns before the 402 does.
  const tone = percent >= 100 ? "#e5484d" : percent >= 80 ? "#f59e0b" : color;
  return (
    <span className="ring" style={{ "--p": percent, "--t": tone } as CSSProperties} role="img" aria-label={`${percent}% лимита`}>
      <b>{value}</b>
    </span>
  );
}

function Metric({ icon: Icon, label, value, note, tint }: { icon: LucideIcon; label: string; value: string; note: string; tint: string }) {
  return (
    <div className="metric" style={{ "--soft": tint } as CSSProperties}>
      <div className="metric-top">
        <Icon size={16} aria-hidden="true" />
        {label}
      </div>
      <strong className="number">{value}</strong>
      <span className="metric-note">{note}</span>
    </div>
  );
}

function FirstSteps({ emailVerified, hasAccount, hasPost, hasScheduled }: { emailVerified: boolean; hasAccount: boolean; hasPost: boolean; hasScheduled: boolean }) {
  const steps = [
    { done: emailVerified, label: "Подтвердить email", hint: "Письмо со ссылкой уже отправлено", href: "/settings" },
    { done: hasAccount, label: "Подключить соцсеть", hint: "Telegram, Instagram, TikTok и другие", href: "/social-accounts" },
    { done: hasPost, label: "Сделать первый пост", hint: "Тема → черновик → правка", href: "/generate" },
    { done: hasScheduled, label: "Запланировать публикацию", hint: "Выберите дату и время в календаре", href: "/calendar" },
  ];
  const doneCount = steps.filter((s) => s.done).length;
  return (
    <section className="panel section">
      <div className="section-head">
        <h2>Первые шаги</h2>
        <span className="tag purple">
          {doneCount} из {steps.length}
        </span>
      </div>
      <div className="progress" aria-hidden="true">
        <span style={{ width: `${(doneCount / steps.length) * 100}%` }} />
      </div>
      <ul className="steps">
        {steps.map((step) => (
          <li key={step.label} className={step.done ? "done" : undefined}>
            <Link href={step.href}>
              <span className="check" aria-hidden="true">
                {step.done && <Check size={14} strokeWidth={3} />}
              </span>
              <span className="steps-text">
                <b>{step.label}</b>
                <small>{step.hint}</small>
              </span>
              <span className="visually-hidden">{step.done ? "Готово" : "Нужно сделать"}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function Dashboard() {
  const { user, token } = useAuth();
  const router = useRouter();
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [posts, setPosts] = useState<Page<Post> | null>(null);
  const [accounts, setAccounts] = useState<SocialAccount[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [idea, setIdea] = useState("");
  const [format, setFormat] = useState<string>("post");

  useEffect(() => {
    // Three independent reads; the page stays useful if one of them fails.
    api.get<AnalyticsSummary>("/analytics/summary?days=7", token).then(setSummary).catch(() => setFailed(true));
    api.get<Page<Post>>("/posts?limit=50", token).then(setPosts).catch(() => setFailed(true));
    api.get<SocialAccount[]>("/social-accounts", token).then(setAccounts).catch(() => setFailed(true));
  }, [token]);

  const upcoming = useMemo(
    () =>
      (posts?.items ?? [])
        .filter((p) => p.status === "scheduled" && new Date(p.scheduled_for).getTime() > Date.now() - 3600 * 1000)
        .sort((a, b) => new Date(a.scheduled_for).getTime() - new Date(b.scheduled_for).getTime())
        .slice(0, 4),
    [posts],
  );
  const recent = useMemo(
    () =>
      (posts?.items ?? [])
        .filter((p) => p.image_url || p.video_url)
        .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
        .slice(0, 3),
    [posts],
  );
  // Only allowances the plan actually has: "0 / 0" (e.g. long AI videos
  // on Pro) is not a limit anyone is using, and an unlimited kind has no ring.
  const limits = useMemo(
    () => (summary?.usage_this_period ?? []).filter((u) => u.limit !== null && u.limit > 0).slice(0, 4),
    [summary],
  );

  function startCreating(event: FormEvent) {
    event.preventDefault();
    const chip = FORMATS.find((f) => f.id === format) ?? FORMATS[0];
    const params = new URLSearchParams(chip.query ?? "");
    // Only the post/picture studio takes the idea along; the others open empty.
    if (idea.trim() && chip.href === "/generate") params.set("topic", idea.trim());
    const query = params.toString();
    router.push(query ? `${chip.href}?${query}` : chip.href);
  }

  const loading = summary === null || posts === null || accounts === null;
  const isNewcomer = !loading && posts.total === 0 && accounts.length === 0;
  const inProgress = (summary?.generations_queued ?? 0) + (summary?.generations_processing ?? 0);

  return (
    <>
      <div className="page-header">
        <div>
          <h1>{greeting()}!</h1>
          <p className="muted">Ваша студия готова к новым идеям.</p>
        </div>
      </div>

      <section className="hero">
        <div className="eyebrow">От идеи до публикации</div>
        <h2 className="hero-title">Что создадим сегодня?</h2>
        <p className="muted">Одна идея. Любой формат. Все ваши площадки.</p>
        <form className="prompt" onSubmit={startCreating}>
          <input
            value={idea}
            onChange={(event) => setIdea(event.target.value)}
            placeholder="Например, запуск новой коллекции…"
            aria-label="Тема контента"
            maxLength={5000}
          />
          <button type="submit" aria-label="Начать создавать">
            <ArrowUp size={18} strokeWidth={2.4} />
          </button>
        </form>
        <div className="chip-row" role="group" aria-label="Формат">
          {FORMATS.map((chip) => {
            const Icon = chip.icon;
            return (
              <button
                key={chip.id}
                type="button"
                className={`chip${format === chip.id ? " selected" : ""}`}
                aria-pressed={format === chip.id}
                onClick={() => setFormat(chip.id)}
              >
                <Icon size={15} aria-hidden="true" />
                {chip.label}
              </button>
            );
          })}
        </div>
      </section>

      {failed && <p className="error">Часть данных не загрузилась — обновите страницу, если цифры выглядят неполными.</p>}

      {loading && !failed && (
        <div className="grid three" aria-busy="true">
          {[0, 1, 2].map((i) => (
            <div key={i} className="skeleton" />
          ))}
        </div>
      )}

      {isNewcomer && user && (
        <FirstSteps emailVerified={user.email_verified} hasAccount={false} hasPost={false} hasScheduled={false} />
      )}

      {summary && !isNewcomer && (
        <>
          <div className="grid three">
            <Metric icon={Send} label="Публикации" value={String(summary.posts_published)} note="за 7 дней" tint="#f0edff" />
            <Metric icon={Sparkles} label="Генерации" value={String(summary.generations_total)} note="за 7 дней" tint="#fff1e7" />
            <Metric
              icon={CircleCheck}
              label="Успешность"
              value={summary.publish_success_rate === null ? "—" : `${Math.round(summary.publish_success_rate * 100)}%`}
              note="публикаций дошли до площадки"
              tint="#eaf7f0"
            />
          </div>

          <div className="split">
            <section className="panel">
              <div className="section-head">
                <h2>Ближайшие публикации</h2>
                <Link href="/calendar" className="quiet-link">
                  Все →
                </Link>
              </div>
              {upcoming.length === 0 ? (
                <div className="empty-inline">
                  <CalendarDays size={22} aria-hidden="true" />
                  <p>Пока ничего не запланировано.</p>
                  <Link href="/generate" className="button-link secondary">
                    Создать пост
                  </Link>
                </div>
              ) : (
                upcoming.map((post) => (
                  <Link key={post.id} href="/calendar" className="list-line">
                    <span className="tag">{whenLabel(post.scheduled_for)}</span>
                    <span className="list-line-text">
                      <b>{snippet(post.text)}</b>
                      <small>
                        {PLATFORM_NAMES[post.platform] ?? post.platform} · {post.account_label}
                      </small>
                    </span>
                  </Link>
                ))
              )}
            </section>

            <section className="panel">
              <div className="section-head">
                <h2>Ваш ресурс</h2>
                <Link href="/billing" className="quiet-link">
                  Тариф →
                </Link>
              </div>
              {limits.length === 0 ? (
                <p className="muted">Лимиты вашего тарифа появятся здесь.</p>
              ) : (
                limits.map((stat) => (
                  <div key={`${stat.event_type}:${stat.content_type ?? ""}`} className="list-line">
                    <Ring value={stat.used} of={stat.limit ?? 0} color={usageColor(stat)} />
                    <span className="list-line-text wrap">
                      <b>{stat.label}</b>
                      <small>
                        {stat.used} / {stat.limit} в этом месяце
                      </small>
                    </span>
                  </div>
                ))
              )}
            </section>
          </div>

          {recent.length > 0 && (
            <section className="section">
              <div className="section-head">
                <h2>Последние работы</h2>
                <Link href="/calendar" className="quiet-link">
                  Открыть календарь →
                </Link>
              </div>
              <div className="grid three">
                {recent.map((post) => (
                  <Link key={post.id} href="/calendar" className="work">
                    <div className="work-media">
                      {post.image_url ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={post.image_url} alt="" loading="lazy" />
                      ) : (
                        <video src={post.video_url ?? undefined} preload="metadata" muted playsInline />
                      )}
                    </div>
                    <div className="work-meta">
                      <b>{snippet(post.text, 48)}</b>
                      <small>
                        {PLATFORM_NAMES[post.platform] ?? post.platform} · {new Date(post.created_at).toLocaleDateString("ru-RU", { day: "numeric", month: "long" })}
                      </small>
                    </div>
                  </Link>
                ))}
              </div>
            </section>
          )}

          {inProgress > 0 && (
            <section className="panel section">
              <div className="section-head">
                <h2>Сейчас в работе</h2>
                <span className="tag purple">{inProgress}</span>
              </div>
              <p className="muted">
                {inProgress === 1 ? "Одна генерация" : `Генераций: ${inProgress}`} готовится — можно продолжать работу, результат появится сам.
              </p>
            </section>
          )}

          {accounts && accounts.length === 0 && (
            <FirstSteps emailVerified={user?.email_verified ?? false} hasAccount={false} hasPost={(posts?.total ?? 0) > 0} hasScheduled={upcoming.length > 0} />
          )}
        </>
      )}
    </>
  );
}
