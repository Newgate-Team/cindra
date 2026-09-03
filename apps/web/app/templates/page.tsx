"use client";

import { useEffect, useMemo, useState, type ChangeEvent, type FormEvent } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { allowedContentTypesFor } from "@/lib/publish-matrix";
import type { LayoutTemplate, SocialAccount } from "@/lib/types";

import { RequireAuth } from "../components/RequireAuth";
import { ReviewAndPublish } from "../components/ReviewAndPublish";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const FORMATS = [
  { value: "square", label: "Квадрат — лента" },
  { value: "story", label: "Вертикаль — сторис" },
  { value: "landscape", label: "Горизонт — Telegram/Facebook" },
];
const THEMES = [
  { value: "dark", label: "Тёмная" },
  { value: "light", label: "Светлая" },
  { value: "ember", label: "Акцентная" },
];

// The preview endpoint returns raw PNG and requires the bearer token,
// which a plain <img src> cannot send. Fetched here and handed to the
// gallery as object URLs -- putting the token in the query string
// instead would leak it into logs and history.
function useTemplatePreviews(templates: LayoutTemplate[], canvasFormat: string, theme: string) {
  const { token } = useAuth();
  const [previews, setPreviews] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!token || templates.length === 0) return;
    let cancelled = false;
    const created: string[] = [];

    Promise.all(
      templates.map(async (template) => {
        const response = await fetch(
          `${API_URL}/content/layout-templates/${template.id}/preview?canvas_format=${canvasFormat}&theme=${theme}`,
          { headers: { Authorization: `Bearer ${token}` } }
        );
        if (!response.ok) return [template.id, ""] as const;
        const url = URL.createObjectURL(await response.blob());
        created.push(url);
        return [template.id, url] as const;
      })
    ).then((entries) => {
      if (cancelled) {
        created.forEach(URL.revokeObjectURL);
        return;
      }
      setPreviews(Object.fromEntries(entries.filter(([, url]) => url)));
    });

    return () => {
      cancelled = true;
      created.forEach(URL.revokeObjectURL);
    };
  }, [templates, canvasFormat, theme, token]);

  return previews;
}

function TemplateStudio() {
  const { token } = useAuth();
  const [templates, setTemplates] = useState<LayoutTemplate[]>([]);
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [accountsLoaded, setAccountsLoaded] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [canvasFormat, setCanvasFormat] = useState("square");
  const [theme, setTheme] = useState("dark");
  const [accent, setAccent] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [targetAccountIds, setTargetAccountIds] = useState<string[]>([]);
  const [backgroundUrl, setBackgroundUrl] = useState<string | null>(null);
  const [uploadingBackground, setUploadingBackground] = useState(false);
  const [renderedUrl, setRenderedUrl] = useState<string | null>(null);
  const [rendering, setRendering] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<LayoutTemplate[]>("/content/layout-templates", token).then(setTemplates).catch(() => {});
    api.get<SocialAccount[]>("/social-accounts", token).then((list) => {
      setAccounts(list);
      setAccountsLoaded(true);
    });
  }, [token]);

  const previews = useTemplatePreviews(templates, canvasFormat, theme);
  const selected = useMemo(
    () => templates.find((t) => t.id === selectedId) ?? null,
    [templates, selectedId]
  );
  // A rendered card is an image, so only accounts that can publish an
  // image are offered -- TikTok is video-only (see publish matrix).
  const imageAccounts = useMemo(
    () => accounts.filter((a) => allowedContentTypesFor([a.platform]).includes("image")),
    [accounts]
  );

  function selectTemplate(id: string) {
    setSelectedId(id);
    setValues({});
    setRenderedUrl(null);
    setError(null);
    setBackgroundUrl(null);
  }

  async function handleBackgroundChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setError(null);
    setUploadingBackground(true);
    try {
      const uploaded = await api.upload<{ background_url: string }>(
        "/content/layout-background",
        file,
        token
      );
      setBackgroundUrl(uploaded.background_url);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить подложку");
    } finally {
      setUploadingBackground(false);
      event.target.value = "";
    }
  }

  async function handleRender(event: FormEvent) {
    event.preventDefault();
    if (!selected) return;
    setError(null);
    setRendering(true);
    setRenderedUrl(null);
    try {
      const result = await api.post<{ image_url: string }>(
        "/content/layout-render",
        {
          template_id: selected.id,
          canvas_format: canvasFormat,
          theme,
          accent: accent || null,
          values,
          background_url: selected.supports_image ? backgroundUrl : null,
        },
        token
      );
      setRenderedUrl(result.image_url);
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        setError("Лимит карточек по тарифу исчерпан. Обновите тариф на странице «Тариф».");
      } else {
        setError(err instanceof ApiError ? err.message : "Не удалось собрать карточку");
      }
    } finally {
      setRendering(false);
    }
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Шаблоны</h1>
          <p className="muted">
            Карточки собираются по макету, а не рисуются моделью: текст встанет ровно так, как вы
            его написали.
          </p>
        </div>
      </div>

      <div className="card">
        <div className="grid-2">
          <label>
            Формат
            <select value={canvasFormat} onChange={(e) => setCanvasFormat(e.target.value)}>
              {FORMATS.map((f) => (
                <option key={f.value} value={f.value}>
                  {f.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Тема
            <select value={theme} onChange={(e) => setTheme(e.target.value)}>
              {THEMES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        <fieldset className="template-gallery">
          <legend>Макет</legend>
          {templates.map((template) => (
            <button
              key={template.id}
              type="button"
              className={`template-card${template.id === selectedId ? " selected" : ""}`}
              onClick={() => selectTemplate(template.id)}
            >
              {previews[template.id] ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={previews[template.id]} alt={template.title} />
              ) : (
                <span className="template-card-placeholder muted">Загружаем превью…</span>
              )}
              <strong>{template.title}</strong>
              <span className="muted">{template.description}</span>
            </button>
          ))}
        </fieldset>
      </div>

      {selected && (
        <form onSubmit={handleRender} className="card" style={{ marginTop: 24 }}>
          <h2>{selected.title}</h2>
          {selected.slots.map((slot) => (
            <label key={slot.name}>
              {slot.label}
              {!slot.required && " (необязательно)"}
              <textarea
                rows={slot.max_length > 100 ? 3 : 1}
                maxLength={slot.max_length}
                value={values[slot.name] ?? ""}
                onChange={(e) => setValues({ ...values, [slot.name]: e.target.value })}
              />
            </label>
          ))}
          {selected.supports_image && (
            <label>
              Подложка — своё изображение
              <input
                type="file"
                accept="image/*"
                onChange={handleBackgroundChange}
                disabled={uploadingBackground}
              />
              {uploadingBackground && <span className="muted">Загружаем…</span>}
              {backgroundUrl && !uploadingBackground && (
                <span className="muted">Подложка загружена</span>
              )}
            </label>
          )}
          <label>
            Акцентный цвет (необязательно)
            <input
              type="color"
              value={accent || "#DA2E2B"}
              onChange={(e) => setAccent(e.target.value.toUpperCase())}
            />
          </label>
          {accent && (
            <button type="button" className="link-button" onClick={() => setAccent("")}>
              Вернуть цвет темы
            </button>
          )}
          {error && <p className="error">{error}</p>}
          <button
            type="submit"
            disabled={rendering || uploadingBackground || (selected.supports_image && !backgroundUrl)}
          >
            {rendering ? "Собираем…" : "Собрать карточку"}
          </button>
          {selected.supports_image && !backgroundUrl && (
            <p className="muted">Этот макет строится поверх изображения — загрузите подложку.</p>
          )}
        </form>
      )}

      {renderedUrl && (
        <div className="card" style={{ marginTop: 24 }}>
          <h2>Готовая карточка</h2>
          {accountsLoaded && imageAccounts.length === 0 ? (
            <>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={renderedUrl} alt="Карточка" style={{ maxWidth: "100%", borderRadius: 8 }} />
              <p className="muted">
                Чтобы опубликовать карточку, подключите соцсеть на странице «Соцсети».
              </p>
            </>
          ) : (
            <>
              <fieldset className="chip-group">
                <legend>Куда опубликовать</legend>
                {imageAccounts.map((a) => (
                  <label key={a.id}>
                    <input
                      type="checkbox"
                      checked={targetAccountIds.includes(a.id)}
                      onChange={(e) =>
                        setTargetAccountIds(
                          e.target.checked
                            ? [...targetAccountIds, a.id]
                            : targetAccountIds.filter((id) => id !== a.id)
                        )
                      }
                    />
                    {a.platform} — {a.display_name ?? a.external_account_id}
                  </label>
                ))}
              </fieldset>
              {targetAccountIds.length > 0 && (
                <ReviewAndPublish
                  imageUrl={renderedUrl}
                  initialCaption=""
                  contentKind="post"
                  accounts={accounts}
                  targetAccountIds={targetAccountIds}
                  // Laid out by code from the user's own words -- not
                  // AI-generated media (CIN-148).
                  aiGenerated={false}
                />
              )}
            </>
          )}
        </div>
      )}
    </>
  );
}

export default function TemplatesPage() {
  return (
    <RequireAuth>
      <TemplateStudio />
    </RequireAuth>
  );
}
