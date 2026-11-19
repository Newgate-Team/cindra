"use client";

import { Sparkles } from "lucide-react";
import Link from "next/link";
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
          <h1>Создано с Cindra</h1>
          <p className="muted">
            {items !== null && total > 0
              ? `Идеи сообщества, которые вдохновляют · ${total}`
              : "Идеи сообщества, которые вдохновляют."}
          </p>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {items === null && !error && (
        <div className="grid three" aria-busy="true">
          <div className="skeleton" />
          <div className="skeleton" />
          <div className="skeleton" />
        </div>
      )}
      {items && items.length === 0 && (
        <section className="panel empty">
          <span className="tile" aria-hidden="true">
            <Sparkles size={26} />
          </span>
          <h2>Пока здесь пусто</h2>
          <p className="muted">Когда участники сообщества сделают первые работы, они появятся в ленте.</p>
        </section>
      )}
      {items && items.length > 0 && (
        <>
          <div className="feed-grid">
            {items.map((item) => (
              <article key={item.id} className="work feed-item">
                {item.image_url && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={item.image_url} alt={item.caption} className="post-media" />
                )}
                {item.video_url && <video src={item.video_url} controls className="post-media" />}
                <div className="work-meta feed-meta">
                  <p>{item.caption}</p>
                  <small>{formatDate(item.created_at)}</small>
                  <Link className="button-link secondary" href={`/generate?topic=${encodeURIComponent(item.caption.slice(0, 500))}`}>
                    Сделать похожее
                  </Link>
                </div>
              </article>
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
