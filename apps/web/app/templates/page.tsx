"use client";

import { ImageUp, LayoutTemplate as LayoutIcon } from "lucide-react";
import { useEffect, useMemo, useState, type ChangeEvent, type FormEvent } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { allowedContentTypesFor } from "@/lib/publish-matrix";
import type { LayoutTemplate, SocialAccount } from "@/lib/types";

import { RequireAuth } from "../components/RequireAuth";
import { ReviewAndPublish } from "../components/ReviewAndPublish";
import { ChipSelect, platformLabel } from "../components/Studio";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const FORMATS = [
  { value: "square", label: "Квадрат · лента" },
  { value: "story", label: "Вертикаль · сторис" },
  { value: "landscape", label: "Горизонт · Telegram, Facebook" },
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
    api
      .get<LayoutTemplate[]>("/content/layout-templates", token)
      .then((list) => {
        setTemplates(list);
        // Open on the first layout so the right side shows a card
        // right away instead of an empty frame.
        setSelectedId((current) => current ?? list[0]?.id ?? null);
      })
      .catch(() => {});
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

  const previewUrl = renderedUrl ?? (selected ? previews[selected.id] : undefined);

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Шаблоны с вашим характером</h1>
          <p className="muted">
            Карточки собираются по макету, а не рисуются моделью: текст встанет ровно так, как вы его написали.
          </p>
        </div>
      </div>

      <div className="studio">
        <div className="studio-form-col">
          <section className="panel studio-form">
            <h2>Настройте карточку</h2>
            <ChipSelect label="Формат" value={canvasFormat} options={FORMATS} onChange={setCanvasFormat} />
            <ChipSelect label="Тема" value={theme} options={THEMES} onChange={setTheme} />
            <div className="field-block" role="group" aria-label="Макет">
              <span className="field-label">Макет</span>
              <div className="template-grid">
                {templates.map((template) => (
                  <button
                    key={template.id}
                    type="button"
                    className={`template-pick${template.id === selectedId ? " selected" : ""}`}
                    aria-pressed={template.id === selectedId}
                    title={template.description}
                    onClick={() => selectTemplate(template.id)}
                  >
                    {previews[template.id] ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={previews[template.id]} alt="" className="template-pick-art" />
                    ) : (
                      <span className="template-pick-art" aria-hidden="true" />
                    )}
                    <b>{template.title}</b>
                  </button>
                ))}
              </div>
              {selected && <p className="muted hint">{selected.description}</p>}
            </div>
          </section>

          {selected && (
            <form onSubmit={handleRender} className="panel studio-form">
              <h2>{selected.title}</h2>
              {selected.slots.map((slot) => (
                <label key={slot.name} className="field-block">
                  <span className="field-label">
                    {slot.label}
                    {!slot.required && " (необязательно)"}
                  </span>
                  <textarea
                    rows={slot.max_length > 100 ? 3 : 1}
                    maxLength={slot.max_length}
                    value={values[slot.name] ?? ""}
                    onChange={(e) => setValues({ ...values, [slot.name]: e.target.value })}
                  />
                </label>
              ))}
              {selected.supports_image && (
                <label className="drop">
                  <ImageUp size={16} aria-hidden="true" /> Подложка — своё изображение
                  <small>
                    {uploadingBackground
                      ? "Загружаем…"
                      : backgroundUrl
                        ? "Подложка загружена"
                        : "Этот макет строится поверх изображения — загрузите подложку."}
                  </small>
                  <input
                    type="file"
                    accept="image/*"
                    onChange={handleBackgroundChange}
                    disabled={uploadingBackground}
                  />
                </label>
              )}
              <div className="field-block">
                <span className="field-label">Акцентный цвет (необязательно)</span>
                <div className="color-row">
                  <input
                    type="color"
                    aria-label="Акцентный цвет"
                    value={accent || "#DA2E2B"}
                    onChange={(e) => setAccent(e.target.value.toUpperCase())}
                  />
                  {accent && (
                    <button type="button" className="ghost" onClick={() => setAccent("")}>
                      Вернуть цвет темы
                    </button>
                  )}
                </div>
              </div>
              {error && <p className="error">{error}</p>}
              <button
                type="submit"
                disabled={rendering || uploadingBackground || (selected.supports_image && !backgroundUrl)}
              >
                <LayoutIcon size={16} aria-hidden="true" /> {rendering ? "Собираем…" : "Собрать карточку"}
              </button>
            </form>
          )}
        </div>

        <section className="panel studio-preview">
          <div className="section-head">
            <h2>{renderedUrl ? "Готовая карточка" : "Превью макета"}</h2>
            <span className={`tag${renderedUrl ? " green" : " purple"}`}>{renderedUrl ? "Готово" : "Пример"}</span>
          </div>
          {previewUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={previewUrl} alt={renderedUrl ? "Карточка" : "Превью макета"} className="template-preview" />
          ) : (
            <div className="skeleton" aria-busy="true" />
          )}
          {!renderedUrl && (
            <p className="muted hint">Заполните поля слева и нажмите «Собрать карточку» — здесь появится готовая карточка.</p>
          )}
          {renderedUrl && (
            <div className="field-block publish-block">
              {accountsLoaded && imageAccounts.length === 0 ? (
                <p className="muted">Чтобы опубликовать карточку, подключите соцсеть на странице «Соцсети».</p>
              ) : (
                <>
                  <fieldset className="field-block plain">
                    <legend className="field-label">Куда опубликовать</legend>
                    <div className="chip-group">
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
                          {platformLabel(a.platform)} — {a.display_name ?? a.external_account_id}
                        </label>
                      ))}
                    </div>
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
        </section>
      </div>
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
