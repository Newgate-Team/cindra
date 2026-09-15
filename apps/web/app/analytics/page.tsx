"use client";

import { useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { AnalyticsSummary } from "@/lib/types";

import { RequireAuth } from "../components/RequireAuth";

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
  // Every ~7th label so a 90-day chart doesn't stack unreadable text.
  const labelEvery = Math.max(1, Math.ceil(data.length / 12));

  return (
    <div className="analytics-chart">
      {data.map((row, index) => (
        <div key={row.day} className="analytics-chart-col">
          <div
            className="analytics-chart-bar"
            style={{ height: `${(row.published / max) * 100}%` }}
            title={`${formatDay(row.day)}: ${row.published}`}
          />
          <span className="analytics-chart-label">
            {index % labelEvery === 0 ? formatDay(row.day) : ""}
          </span>
        </div>
      ))}
    </div>
  );
}

function UsageRow({ stat }: { stat: AnalyticsSummary["usage_this_period"][number] }) {
  const pct = stat.limit ? Math.min(100, (stat.used / stat.limit) * 100) : 0;
  return (
    <div className="analytics-usage-row">
      <div className="analytics-usage-row-header">
        <span>{stat.label}</span>
        <span className="muted">
          {stat.used} {stat.limit !== null ? `/ ${stat.limit}` : "· без лимита"}
        </span>
      </div>
      {stat.limit !== null && (
        <div className="progress-track">
          <div className="progress-fill" style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
  );
}

function AnalyticsDashboard() {
  const { token } = useAuth();
  const [days, setDays] = useState(30);
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSummary(null);
    api
      .get<AnalyticsSummary>(`/analytics/summary?days=${days}`, token)
      .then(setSummary)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Не удалось загрузить"));
  }, [token, days]);

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Аналитика</h1>
          <p className="muted">Публикации и генерации за выбранный период</p>
        </div>
        <div className="page-header-actions">
          {PERIOD_OPTIONS.map((option) => (
            <button
              key={option.days}
              className={option.days === days ? "" : "secondary"}
              onClick={() => setDays(option.days)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="error">{error}</p>}
      {!summary && !error && <p className="muted">Загрузка…</p>}

      {summary && (
        <>
          <div className="tile-grid">
            <div className="card">
              <div className="tile-header-body">
                <strong>Публикации</strong>
              </div>
              <p className="analytics-big-number">{summary.posts_published}</p>
              <p className="muted">
                опубликовано · {summary.posts_failed} ошибок · {summary.posts_scheduled} запланировано
              </p>
              <p className="muted">Успешность: {formatPercent(summary.publish_success_rate)}</p>
            </div>
            <div className="card">
              <div className="tile-header-body">
                <strong>Генерации</strong>
              </div>
              <p className="analytics-big-number">{summary.generations_completed}</p>
              <p className="muted">
                завершено · {summary.generations_failed} ошибок · {summary.generations_flagged} отклонено модерацией
              </p>
              <p className="muted">Успешность: {formatPercent(summary.generation_success_rate)}</p>
            </div>
          </div>

          <div className="card">
            <div className="tile-header-body">
              <strong>Публикации по дням</strong>
            </div>
            <DailyChart data={summary.daily_published_posts} />
          </div>

          <div className="tile-grid">
            <div className="card">
              <div className="tile-header-body">
                <strong>По площадкам</strong>
              </div>
              {summary.posts_by_platform.length === 0 && <p className="muted">Нет данных за период</p>}
              {summary.posts_by_platform.map((row) => (
                <div key={row.platform} className="analytics-list-row">
                  <span>{PLATFORM_LABELS[row.platform] ?? row.platform}</span>
                  <span className="muted">
                    {row.published} опубликовано{row.failed > 0 ? ` · ${row.failed} ошибок` : ""}
                  </span>
                </div>
              ))}
            </div>

            <div className="card">
              <div className="tile-header-body">
                <strong>По типу контента (генерации)</strong>
              </div>
              {summary.generations_by_content_type.length === 0 && (
                <p className="muted">Нет данных за период</p>
              )}
              {summary.generations_by_content_type.map((row) => (
                <div key={row.content_type} className="analytics-list-row">
                  <span>{CONTENT_TYPE_LABELS[row.content_type] ?? row.content_type}</span>
                  <span className="muted">
                    {row.completed} завершено{row.failed > 0 ? ` · ${row.failed} ошибок` : ""}
                  </span>
                </div>
              ))}
            </div>

            <div className="card">
              <div className="tile-header-body">
                <strong>По виду публикации</strong>
              </div>
              {summary.posts_by_content_kind.length === 0 && <p className="muted">Нет данных за период</p>}
              {summary.posts_by_content_kind.map((row) => (
                <div key={row.content_kind} className="analytics-list-row">
                  <span>{row.content_kind}</span>
                  <span className="muted">{row.count}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="card">
            <div className="tile-header-body">
              <strong>Использование тарифа в этом месяце</strong>
            </div>
            {summary.usage_this_period.map((stat) => (
              <UsageRow key={`${stat.event_type}-${stat.content_type ?? ""}`} stat={stat} />
            ))}
          </div>
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
