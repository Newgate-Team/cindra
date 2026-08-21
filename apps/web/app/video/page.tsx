"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { RequireAuth } from "../components/RequireAuth";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { VideoProject } from "@/lib/types";

const STATUS_LABELS: Record<VideoProject["status"], string> = {
  draft: "Черновик",
  script_ready: "Сценарий готов",
  brief_ready: "Бриф готов",
  video_ready: "Видео готово",
};

function VideoProjectsPage() {
  const { token } = useAuth();
  const router = useRouter();
  const [projects, setProjects] = useState<VideoProject[] | null>(null);
  const [topic, setTopic] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (!token) return;
    api
      .get<VideoProject[]>("/video-projects", token)
      .then(setProjects)
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Не удалось загрузить проекты")
      );
  }, [token]);

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setCreating(true);
    try {
      const project = await api.post<VideoProject>("/video-projects", { topic }, token);
      router.push(`/video/${project.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось создать проект");
      setCreating(false);
    }
  }

  return (
    <>
      <h1>Видео</h1>
      <p className="muted">
        Студия: опишите продукт или идею — получите сценарий, выберите стиль и заберите
        производственный бриф. Готовый ролик можно загрузить обратно и опубликовать.
      </p>
      <form onSubmit={handleCreate}>
        <label>
          О чём видео
          <textarea
            required
            rows={3}
            placeholder="Продукт, идея или промпт — из этого соберём сценарий"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={creating}>
          {creating ? "Создаём…" : "Новый проект"}
        </button>
      </form>
      {projects === null ? (
        <p className="muted">Загружаем…</p>
      ) : projects.length === 0 ? (
        <p className="muted">Проектов пока нет — создайте первый.</p>
      ) : (
        <div>
          {projects.map((project) => (
            <Link key={project.id} href={`/video/${project.id}`} className="card list-row">
              <div className="list-row-body">
                <strong>{project.topic.length > 120 ? `${project.topic.slice(0, 120)}…` : project.topic}</strong>
                <p className="muted list-row-meta">
                  <span>{new Date(project.created_at).toLocaleString("ru-RU")}</span>
                </p>
              </div>
              <div className="list-row-side">
                <span className={`badge ${project.status}`}>{STATUS_LABELS[project.status]}</span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}

export default function VideoPage() {
  return (
    <RequireAuth>
      <VideoProjectsPage />
    </RequireAuth>
  );
}
