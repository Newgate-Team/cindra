"use client";

import { useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { Post } from "@/lib/types";

import { RequireAuth } from "../components/RequireAuth";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU");
}

function CalendarList() {
  const { token } = useAuth();
  const [posts, setPosts] = useState<Post[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<Post[]>("/posts", token)
      .then(setPosts)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Не удалось загрузить"));
  }, [token]);

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
              </tr>
            ))}
          </tbody>
        </table>
      )}
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
