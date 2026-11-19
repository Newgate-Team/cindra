"use client";

import { ArrowRight, ArrowUpRight, Clapperboard, Scissors, UserRound } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type CSSProperties, type FormEvent } from "react";

import { RequireAuth } from "../components/RequireAuth";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { Page, VideoProject } from "@/lib/types";

// /video-projects is paginated (backend caps the response, same as
// /posts and /feed) -- this page has no pagination UI yet, so it just
// asks for the largest page the API allows. Anyone with more draft
// projects than that only sees the newest MAX_PAGE_SIZE until this
// page grows real pagination controls.
const MAX_PAGE_SIZE = 100;

const STATUS_LABELS: Record<VideoProject["status"], string> = {
  draft: "Черновик",
  script_ready: "Сценарий готов",
  brief_ready: "Бриф готов",
  video_ready: "Видео готово",
};

function VideoProjectsPage() {
  const { token, user } = useAuth();
  const router = useRouter();
  const [projects, setProjects] = useState<VideoProject[] | null>(null);
  const [topic, setTopic] = useState("");
  // The dashboard's ideas hand a topic over as ?topic= (read once).
  const prefilled = useRef(false);
  useEffect(() => {
    if (prefilled.current) return;
    prefilled.current = true;
    const idea = new URLSearchParams(window.location.search).get("topic");
    if (idea) setTopic(idea.slice(0, 5000));
  }, []);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (!token) return;
    api
      .get<Page<VideoProject>>(`/video-projects?limit=${MAX_PAGE_SIZE}`, token)
      .then((page) => setProjects(page.items))
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Не удалось загрузить проекты")
      );
  }, [token]);

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setCreating(true);
    try {
      const project = await api.post<VideoProject>(
        "/video-projects",
        { topic, brand_guide: user?.brand_guide ?? null },
        token
      );
      router.push(`/video/${project.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось создать проект");
      setCreating(false);
    }
  }

  const TONES = ["violet", "sun", "leaf"] as const;
  const ENTRIES = [
    {
      href: "/video/avatar",
      icon: UserRound,
      title: "Аватар-видео",
      text: "Ваш голос и цифровой ведущий: ролик по сценарию, сразу в нескольких вариантах (HeyGen).",
    },
    {
      href: "/video/montage",
      icon: Scissors,
      title: "Монтаж в DaVinci",
      text: "Клипы, музыка и субтитры — в одном проекте, остаётся только дожать.",
    },
  ];

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Видео, в которых есть идея</h1>
          <p className="muted">От сценария до финального кадра — в вашей студии.</p>
        </div>
      </div>

      <form onSubmit={handleCreate} className="hero compact">
        <div className="eyebrow">Новый проект</div>
        <h2>О чём будет ролик?</h2>
        <div className="prompt">
          <input
            required
            aria-label="О чём видео"
            placeholder="Продукт, идея или промпт — из этого соберём сценарий"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
          />
          <button type="submit" disabled={creating} aria-label="Создать проект">
            <ArrowUpRight size={20} aria-hidden="true" />
          </button>
        </div>
        {error && <p className="error">{error}</p>}
      </form>

      <div className="grid two">
        {ENTRIES.map(({ href, icon: Icon, title, text }) => (
          <Link key={href} href={href} className="panel entry">
            <span className="tile" style={{ "--t": "#ed4c88" } as CSSProperties} aria-hidden="true">
              <Icon size={17} />
            </span>
            <h3>{title}</h3>
            <p className="muted">{text}</p>
            <span className="entry-go">
              Начать <ArrowRight size={14} aria-hidden="true" />
            </span>
          </Link>
        ))}
      </div>

      <div className="section-head section">
        <h2>Ваши проекты</h2>
        {projects && projects.length > 0 && <span className="tag">{projects.length} шт.</span>}
      </div>
      {projects === null ? (
        <div className="grid three" aria-busy="true">
          <div className="skeleton" />
          <div className="skeleton" />
          <div className="skeleton" />
        </div>
      ) : projects.length === 0 ? (
        <section className="panel empty">
          <span className="tile" aria-hidden="true">
            <Clapperboard size={26} />
          </span>
          <h3>Здесь начнётся ваша история</h3>
          <p className="muted">Опишите идею выше — первый проект появится в этом списке.</p>
        </section>
      ) : (
        <div className="grid three">
          {projects.map((project, index) => (
            <Link key={project.id} href={`/video/${project.id}`} className="work">
              <div className={`poster ${TONES[index % TONES.length]}`}>
                <small>{STATUS_LABELS[project.status].toUpperCase()}</small>
                <b>{project.topic.length > 54 ? `${project.topic.slice(0, 54)}…` : project.topic}</b>
                <small>{new Date(project.created_at).toLocaleDateString("ru-RU")}</small>
              </div>
              <div className="work-meta">
                <b>{project.topic.length > 40 ? `${project.topic.slice(0, 40)}…` : project.topic}</b>
                <small>
                  <span className={`badge ${project.status}`}>{STATUS_LABELS[project.status]}</span>
                </small>
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
