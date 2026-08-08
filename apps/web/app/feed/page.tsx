"use client";

import { useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { FeedItem, Page } from "@/lib/types";

import { RequireAuth } from "../components/RequireAuth";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU");
}

const PAGE_SIZE = 20;

// Shared feed across every user's generated image/video content
// (CIN-109) -- not a personal history, intentionally no account/owner
// attribution shown (see FeedItemOut in the API, which already
// excludes it server-side).
function FeedList() {
  const { token } = useAuth();
  const [page, setPage] = useState<Page<FeedItem> | null>(null);
  const [pageIndex, setPageIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<Page<FeedItem>>(`/feed?limit=${PAGE_SIZE}&offset=${pageIndex * PAGE_SIZE}`, token)
      .then(setPage)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Не удалось загрузить"));
  }, [token, pageIndex]);

  const items = page?.items ?? null;
  const total = page?.total ?? 0;
  const rangeStart = total === 0 ? 0 : pageIndex * PAGE_SIZE + 1;
  const rangeEnd = Math.min(total, (pageIndex + 1) * PAGE_SIZE);
  const hasNextPage = rangeEnd < total;

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Лента</h1>
          {items !== null && (
            <p className="muted">{total > 0 ? `${total} публикаций от всех пользователей` : "Пока никто ничего не сгенерировал"}</p>
          )}
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {items === null && !error && <p className="muted">Загрузка…</p>}
      {items && items.length > 0 && (
        <>
          <div className="feed-grid">
            {items.map((item) => (
              <div key={item.id} className="card">
                {item.image_url && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={item.image_url} alt={item.caption} style={{ maxWidth: "100%", borderRadius: 8 }} />
                )}
                {item.video_url && (
                  <video src={item.video_url} controls style={{ maxWidth: "100%", borderRadius: 8 }} />
                )}
                <p>{item.caption}</p>
                <p className="muted">{formatDate(item.created_at)}</p>
              </div>
            ))}
          </div>
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

export default function FeedPage() {
  return (
    <RequireAuth>
      <FeedList />
    </RequireAuth>
  );
}
