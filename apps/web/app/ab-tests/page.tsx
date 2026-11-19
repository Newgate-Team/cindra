"use client";

import { FlaskConical } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { TONE_OPTIONS } from "@/lib/tones";
import type { ABTest } from "@/lib/types";

import { RequireAuth } from "../components/RequireAuth";
import { ChipSelect } from "../components/Studio";

const VARIANT_COUNT_OPTIONS = [2, 3, 4, 5];

function CreateABTestForm() {
  const { token, user } = useAuth();
  const router = useRouter();
  const [topic, setTopic] = useState("");
  const [tone, setTone] = useState(user?.default_tone ?? "");
  const [brandGuide, setBrandGuide] = useState(user?.brand_guide ?? "");
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
          <h1>Найдите сильную идею</h1>
          <p className="muted">Сравните варианты текста для одной задачи и выберите тот, что звучит точнее.</p>
        </div>
      </div>

      <form onSubmit={handleSubmit} className="panel studio-form wide">
        <label className="field-block">
          <span className="field-label">Тема</span>
          <textarea
            required
            rows={3}
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="например, запуск осенней коллекции кофе"
          />
        </label>
        <ChipSelect label="Тон" value={tone} options={TONE_OPTIONS} onChange={setTone} />
        <ChipSelect
          label="Сколько вариантов"
          value={String(variantCount)}
          options={VARIANT_COUNT_OPTIONS.map((count) => ({ value: String(count), label: String(count) }))}
          onChange={(value) => setVariantCount(Number(value))}
        />
        <details className="field-block">
          <summary>Бренд-гайд</summary>
          <label>
            <span className="visually-hidden">Бренд-гайд</span>
            <textarea
              rows={3}
              value={brandGuide}
              onChange={(e) => setBrandGuide(e.target.value)}
              placeholder="тон и стиль, которых нужно придерживаться"
            />
          </label>
        </details>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={submitting}>
          <FlaskConical size={16} aria-hidden="true" /> {submitting ? "Запускаем…" : "Сгенерировать варианты"}
        </button>
      </form>
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
