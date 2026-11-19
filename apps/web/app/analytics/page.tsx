"use client";

import { CircleCheck, Infinity, Send, Sparkles } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type CSSProperties } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { AnalyticsSummary } from "@/lib/types";

import { RequireAuth } from "../components/RequireAuth";
import { Metric, Ring, usageColor } from "../components/Widgets";

const PERIOD_OPTIONS = [
  { days: 7, label: "7 дней" },
  { days: 30, label: "30 дней" },
  { days: 90, label: "90 дней" },
];

const PLATFORM_LABELS: Record<string, string> = {
  telegram: "Telegram",
  instagram: "Instagram",
  facebook: "Facebook",
  tiktok: "TikTok",
  youtube: "YouTube",
  linkedin: "LinkedIn",
  reddit: "Reddit",
  twitter: "X",
};

const CONTENT_KIND_LABELS: Record<string, string> = {
  post: "Пост",
  story: "Сторис",
  video_script: "Сценарий видео",
};

const CONTENT_TYPE_LABELS: Record<string, string> = {
  text: "Текст",
  image: "Изображение",
  video: "Видео",
};

function formatPercent(rate: number | null): string {
  if (rate === null) return "—";
  return `${Math.round(rate * 100)}%`;
}

function formatDay(iso: string): string {
  return new Date(iso).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
}

function DailyChart({ data }: { data: AnalyticsSummary["daily_published_posts"] }) {
  const max = Math.max(1, ...data.map((row) => row.published));
  // At most ~7 date labels: with every column labelled a 30- or 90-day
  // chart printed its dates on top of each other.
  const labelEvery = Math.max(1, Math.ceil(data.length / 7));
  // Value above the bar only when the bars are wide enough to hold it.
  const showValues = data.length <= 14;
  const total = data.reduce((sum, row) => sum + row.published, 0);

  return (
    <>
    <div
      className="bars"
      role="img"
      aria-label={`Публикации по дням, всего ${total}: ${data.map((row) => `${formatDay(row.day)} — ${row.published}`).join(", ")}`}
    >
      {data.map((row, index) => (
        <div
          key={row.day}
          className={`bar${row.published === 0 ? " zero" : ""}`}
          style={{ "--h": `${Math.max(2, (row.published / max) * 100)}%` } as CSSProperties}
          title={`${formatDay(row.day)}: ${row.published}`}
        >
          {showValues && row.published > 0 && <b>{row.published}</b>}
          {index % labelEvery === 0 && <small>{formatDay(row.day)}</small>}
        </div>
      ))}
    </div>
    {total === 0 && <p className="muted hint">За этот период ещё не было публикаций — столбцы появятся, как только они уйдут.</p>}
    </>
  );
}

function AnalyticsDashboard() {
  const { token } = useAuth();
  const [days, setDays] = useState(30);
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSummary(null);
    setError(null);
    api
      .get<AnalyticsSummary>(`/analytics/summary?days=${days}`, token)
      .then(setSummary)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Не удалось загрузить"));
  }, [token, days]);

  const platformMax = Math.max(1, ...(summary?.posts_by_platform.map((row) => row.published + row.failed) ?? [1]));
  const limited = summary?.usage_this_period.filter((stat) => stat.limit !== null && stat.limit > 0) ?? [];

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Ваш контент в цифрах</h1>
          <p className="muted">Результаты публикаций и расход ресурсов студии.</p>
        </div>
      </div>
      <div className="chip-row" role="group" aria-label="Период">
        {PERIOD_OPTIONS.map((option) => (
          <button
            key={option.days}
            type="button"
            className={`chip${option.days === days ? " selected" : ""}`}
            aria-pressed={option.days === days}
            onClick={() => setDays(option.days)}
          >
            {option.label}
          </button>
        ))}
      </div>

      {error && <p className="error section">{error}</p>}
      {!summary && !error && (
        <div className="grid three" aria-busy="true">
          <div className="skeleton" />
          <div className="skeleton" />
          <div className="skeleton" />
        </div>
      )}

      {summary && (
        <>
          <div className="grid three">
            <Metric
              icon={Send}
              label="Публикации"
              value={String(summary.posts_published)}
              note={`${summary.posts_scheduled} запланировано · ${summary.posts_failed} ошибок`}
              tint="#f0edff"
            />
            <Metric
              icon={Sparkles}
              label="Генерации"
              value={String(summary.generations_completed)}
              note={`${summary.generations_failed} ошибок · ${summary.generations_flagged} отклонено модерацией`}
              tint="#fff1e7"
            />
            <Metric
              icon={CircleCheck}
              label="Успешность публикаций"
              value={formatPercent(summary.publish_success_rate)}
              note={`генераций: ${formatPercent(summary.generation_success_rate)}`}
              tint="#eaf7f0"
            />
          </div>

          <section className="panel section">
            <h2>Публикации по дням</h2>
            <DailyChart data={summary.daily_published_posts} />
          </section>

          <div className="grid two section">
            <section className="panel">
              <h2>По площадкам</h2>
              {summary.posts_by_platform.length === 0 && <p className="muted">Нет данных за период</p>}
              {summary.posts_by_platform.map((row) => (
                <div key={row.platform} className="hbar">
                  <div className="hbar-head">
                    <span>{PLATFORM_LABELS[row.platform] ?? row.platform}</span>
                    <span className="muted">
                      {row.published}
                      {row.failed > 0 ? ` · ${row.failed} ошибок` : ""}
                    </span>
                  </div>
                  <div className="progress">
                    <span style={{ width: `${((row.published + row.failed) / platformMax) * 100}%` }} />
                  </div>
                </div>
              ))}
            </section>

            <section className="panel">
              <h2>Что генерируете</h2>
              {summary.generations_by_content_type.length === 0 && <p className="muted">Нет данных за период</p>}
              {summary.generations_by_content_type.map((row) => (
                <div key={row.content_type} className="list-line">
                  <div className="list-line-text">
                    <b>{CONTENT_TYPE_LABELS[row.content_type] ?? row.content_type}</b>
                    <small>{row.failed > 0 ? `${row.failed} ошибок` : "без ошибок"}</small>
                  </div>
                  <span className="tag green">{row.completed} готово</span>
                </div>
              ))}
              {summary.posts_by_content_kind.length > 0 && (
                <>
                  <h3 className="section">Вид публикаций</h3>
                  <div className="chip-row">
                    {summary.posts_by_content_kind.map((row) => (
                      <span key={row.content_kind} className="tag">
                        {CONTENT_KIND_LABELS[row.content_kind] ?? row.content_kind} · {row.count}
                      </span>
                    ))}
                  </div>
                </>
              )}
            </section>
          </div>

          <section className="panel section">
            <div className="section-head">
              <h2>Ресурс тарифа в этом месяце</h2>
              <Link href="/billing" className="quiet-link">
                Тариф →
              </Link>
            </div>
            <div className="ring-grid">
              {limited.map((stat) => (
                <div key={`${stat.event_type}-${stat.content_type ?? ""}`} className="list-line">
                  <Ring value={stat.used} of={stat.limit ?? 0} color={usageColor(stat)} />
                  <div className="list-line-text">
                    <b>{stat.label}</b>
                    <small>
                      {stat.used} / {stat.limit}
                    </small>
                  </div>
                </div>
              ))}
              {summary.usage_this_period
                .filter((stat) => stat.limit === null)
                .map((stat) => (
                  <div key={`${stat.event_type}-${stat.content_type ?? ""}`} className="list-line">
                    <span className="tile" aria-hidden="true">
                      <Infinity size={18} />
                    </span>
                    <div className="list-line-text">
                      <b>{stat.label}</b>
                      <small>{stat.used} · без лимита</small>
                    </div>
                  </div>
                ))}
            </div>
          </section>
        </>
      )}
    </>
  );
}

export default function AnalyticsPage() {
  return (
    <RequireAuth>
      <AnalyticsDashboard />
    </RequireAuth>
  );
}
