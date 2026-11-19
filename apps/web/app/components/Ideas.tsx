"use client";

import { Clapperboard, Image as ImageIcon, Lightbulb, RefreshCw, Sparkles, Type, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

interface Idea {
  title: string;
  hook: string;
  format: "post" | "image" | "video";
}

const STORAGE_KEY = "cindra-week-ideas";

const FORMAT_META: Record<Idea["format"], { label: string; icon: LucideIcon; action: string; color: string }> = {
  post: { label: "Пост", icon: Type, action: "Сделать пост", color: "#7c5cff" },
  image: { label: "Картинка", icon: ImageIcon, action: "Сделать картинку", color: "#ed4c88" },
  video: { label: "Видео", icon: Clapperboard, action: "Сделать видео", color: "#e88a3d" },
};

function hrefFor(idea: Idea): string {
  const topic = encodeURIComponent(`${idea.title}. ${idea.hook}`.slice(0, 600));
  if (idea.format === "video") return `/video?topic=${topic}`;
  return `/generate?topic=${topic}${idea.format === "image" ? "&format=image" : ""}`;
}

// "What shall I post this week?" -- the part of content work that is
// staring at an empty box. One click returns six concrete ideas, each
// of which opens the right studio with the topic already filled in.
export function Ideas() {
  const { token } = useAuth();
  const [ideas, setIdeas] = useState<Idea[] | null>(null);
  const [focus, setFocus] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Keep the last batch across navigation: ideas are worth coming back to.
  useEffect(() => {
    try {
      const saved = sessionStorage.getItem(STORAGE_KEY);
      if (saved) setIdeas(JSON.parse(saved) as Idea[]);
    } catch {
      // Blocked storage or a corrupt value: start empty.
    }
  }, []);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const result = await api.post<{ ideas: Idea[] }>("/content/ideas", { focus: focus.trim() || null }, token);
      setIdeas(result.ideas);
      try {
        sessionStorage.setItem(STORAGE_KEY, JSON.stringify(result.ideas));
      } catch {
        // Storage is a convenience only.
      }
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 429
          ? "Идеи уже запрашивали несколько раз — попробуйте чуть позже."
          : err instanceof ApiError
            ? err.message
            : "Не удалось придумать идеи"
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="panel section ideas">
      <div className="section-head">
        <h2>
          <Lightbulb size={18} aria-hidden="true" className="inline-icon" /> Идеи на эту неделю
        </h2>
      </div>
      <form onSubmit={handleSubmit} className="ideas-form">
        <input
          aria-label="О чём эта неделя (необязательно)"
          placeholder={ideas ? "Другой фокус недели — по желанию" : "О чём эта неделя? Например: запуск осенней коллекции (необязательно)"}
          value={focus}
          maxLength={500}
          onChange={(e) => setFocus(e.target.value)}
        />
        <button type="submit" disabled={loading}>
          {ideas ? <RefreshCw size={16} aria-hidden="true" /> : <Sparkles size={16} aria-hidden="true" />}{" "}
          {loading ? "Думаем…" : ideas ? "Другие идеи" : "Предложить идеи"}
        </button>
      </form>
      {error && <p className="error">{error}</p>}
      {loading && !ideas && (
        <div className="grid three" aria-busy="true">
          <div className="skeleton" />
          <div className="skeleton" />
          <div className="skeleton" />
        </div>
      )}
      {ideas && (
        <div className="idea-grid">
          {ideas.map((idea, index) => {
            const meta = FORMAT_META[idea.format];
            const Icon = meta.icon;
            return (
              <article key={`${idea.title}-${index}`} className="idea">
                <span className="idea-format" style={{ color: meta.color }}>
                  <Icon size={14} aria-hidden="true" /> {meta.label}
                </span>
                <h3>{idea.title}</h3>
                <p className="muted">«{idea.hook}»</p>
                <Link href={hrefFor(idea)} className="button-link secondary">
                  {meta.action}
                </Link>
              </article>
            );
          })}
        </div>
      )}
      {!ideas && !loading && (
        <p className="muted hint">
          Шесть конкретных тем с первой фразой — с учётом вашего бренд-гайда и того, что вы уже делали. Каждую можно
          открыть в студии одним нажатием.
        </p>
      )}
    </section>
  );
}
