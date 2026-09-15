"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { ABTest } from "@/lib/types";

import { RequireAuth } from "../components/RequireAuth";

// Mirrors prompts.TONE_GUIDANCE keys on the backend, same as
// GenerationForm's own copy of this list.
const TONE_OPTIONS = [
  { value: "", label: "По умолчанию" },
  { value: "expert", label: "Экспертный" },
  { value: "conversational", label: "Разговорный" },
  { value: "provocative", label: "Провокационный" },
  { value: "storytelling", label: "Сторителлинг" },
];

const VARIANT_COUNT_OPTIONS = [2, 3, 4, 5];

function CreateABTestForm() {
  const { token } = useAuth();
  const router = useRouter();
  const [topic, setTopic] = useState("");
  const [tone, setTone] = useState("");
  const [brandGuide, setBrandGuide] = useState("");
  const [variantCount, setVariantCount] = useState(2);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const test = await api.post<ABTest>(
        "/ab-tests",
        {
          topic,
          tone: tone || null,
          brand_guide: brandGuide || null,
          variant_count: variantCount,
        },
        token
      );
      router.push(`/ab-tests/${test.id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        setError("Лимит генераций по тарифу исчерпан. Обновите тариф на странице «Тариф».");
      } else {
        setError(err instanceof ApiError ? err.message : "Не удалось запустить A/B тест");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1>A/B тест</h1>
          <p className="muted">
            Сгенерируйте несколько вариантов текста для одной задачи и сравните их
          </p>
        </div>
      </div>

      <div className="card">
        <form onSubmit={handleSubmit}>
          <label>
            Тема
            <textarea
              required
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="например, запуск осенней коллекции кофе"
            />
          </label>
          <label>
            Тон (необязательно)
            <select value={tone} onChange={(e) => setTone(e.target.value)}>
              {TONE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Бренд-гайд (необязательно)
            <textarea
              value={brandGuide}
              onChange={(e) => setBrandGuide(e.target.value)}
              placeholder="тон и стиль, которых нужно придерживаться"
            />
          </label>
          <label>
            Количество вариантов
            <select
              value={variantCount}
              onChange={(e) => setVariantCount(Number(e.target.value))}
            >
              {VARIANT_COUNT_OPTIONS.map((count) => (
                <option key={count} value={count}>
                  {count}
                </option>
              ))}
            </select>
          </label>
          {error && <p className="error">{error}</p>}
          <button type="submit" disabled={submitting}>
            {submitting ? "Запускаем…" : "Сгенерировать варианты"}
          </button>
        </form>
      </div>
    </>
  );
}

export default function ABTestsPage() {
  return (
    <RequireAuth>
      <CreateABTestForm />
    </RequireAuth>
  );
}
