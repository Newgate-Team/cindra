"use client";

import { Paperclip, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { CONTENT_KIND_LABELS, allowedContentKindsFor, allowedContentTypesFor } from "@/lib/publish-matrix";
import type {
  Attachment,
  GenerationContentType,
  GenerationJob,
  ImageTemplate,
  SocialAccount,
  SocialPlatform,
} from "@/lib/types";

import { Poster } from "./Brand";
import { ReviewAndPublish } from "./ReviewAndPublish";
import { ChipSelect, PostCard, platformLabel } from "./Studio";

const TERMINAL_STATUSES = new Set(["completed", "failed", "flagged"]);

// CIN-138: mirrors prompts.TONE_GUIDANCE keys on the backend.
const TONE_OPTIONS = [
  { value: "", label: "По умолчанию" },
  { value: "expert", label: "Экспертный" },
  { value: "conversational", label: "Разговорный" },
  { value: "provocative", label: "Провокационный" },
  { value: "storytelling", label: "Сторителлинг" },
];
const POLL_INTERVAL_MS = 2000;

const JOB_STATUS_LABELS: Record<string, string> = {
  queued: "В очереди",
  processing: "Генерируем",
  completed: "Готово",
  failed: "Ошибка",
  flagged: "Отклонено",
};

// No target account chosen yet means no platform to constrain the
// choice by -- these are the unconstrained fallbacks, mirroring the
// union of what lib/publish-matrix.ts's ALLOWED_CONTENT_TYPES/
// ALLOWED_CONTENT_KINDS allow across every platform.
const ALL_CONTENT_TYPES: GenerationContentType[] = ["text", "image", "video"];
const ALL_CONTENT_KINDS_BY_TYPE: Record<GenerationContentType, string[]> = {
  text: ["post", "video_script"],
  image: ["post", "story"],
  video: ["post", "story"],
};

function platformsFor(ids: string[], accounts: SocialAccount[]): SocialPlatform[] {
  const set = new Set<SocialPlatform>();
  for (const id of ids) {
    const account = accounts.find((a) => a.id === id);
    if (account) set.add(account.platform);
  }
  return [...set];
}

// Video scripts aren't meant to be posted anywhere directly (CIN-130)
// -- this is the post-generation step for that case: edit, then
// download as a .txt file instead of picking a target account and
// publishing. Plain .txt rather than .md: every phone/OS opens .txt
// in some text viewer with no extra app, whereas .md commonly has no
// default handler at all on mobile.
function ReviewAndDownload({ job, initialCaption }: { job: GenerationJob; initialCaption: string }) {
  const [text, setText] = useState(job.output_payload?.text ?? initialCaption);

  function handleDownload() {
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `video-script-${new Date().toISOString().slice(0, 10)}.txt`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  return (
    <div>
      <label>
        Сценарий (можно отредактировать перед скачиванием)
        <textarea rows={12} value={text} onChange={(e) => setText(e.target.value)} />
      </label>
      <button type="button" onClick={handleDownload}>
        Скачать сценарий (.txt)
      </button>
    </div>
  );
}

const CONTENT_TYPE_LABELS: Record<GenerationContentType, string> = {
  text: "Текст",
  image: "Изображение",
  video: "Видео",
};

// .doc (legacy binary Word) is deliberately excluded -- the backend
// parser (python-docx) only reads the OOXML .docx format.
const ATTACHMENT_ACCEPT = ".txt,.md,.pdf,.docx,image/*,video/*,audio/*";

const ATTACHMENT_TYPE_LABELS: Record<Attachment["attachment_type"], string> = {
  image: "фото",
  video: "видео",
  audio: "аудио",
  document: "документ",
};

// Mirrors app/content_pipeline/attachments.py's validate_attachment_set
// (CIN-107): up to 5 attachments total, any mix of document/image, but
// video and audio are capped at 1 each below that total.
const MAX_TOTAL_ATTACHMENTS = 5;
const PER_TYPE_ATTACHMENT_CAPS: Partial<Record<Attachment["attachment_type"], number>> = {
  video: 1,
  audio: 1,
};

type NamedAttachment = Attachment & { name: string };

function attachmentCapReached(attachments: NamedAttachment[], attachmentType: Attachment["attachment_type"]): boolean {
  if (attachments.length >= MAX_TOTAL_ATTACHMENTS) return true;
  const perTypeCap = PER_TYPE_ATTACHMENT_CAPS[attachmentType];
  if (perTypeCap === undefined) return false;
  return attachments.filter((a) => a.attachment_type === attachmentType).length >= perTypeCap;
}

export interface GenerationFormProps {
  heading: string;
  subtitle: string;
  // When set, the Формат/Тип контента selectors are hidden and the
  // form stays on this fixed combination -- used by the dedicated
  // "Сценарий видео" page (CIN-129), which is just this same flow
  // pinned to content_type="text"/content_kind="video_script" instead
  // of exposing that combination as an option on the main page.
  lockedContentType?: GenerationContentType;
  lockedContentKind?: string;
  // Kinds to hide from the Тип контента dropdown when NOT locked --
  // e.g. the main Генерация page excludes "video_script" now that it
  // has its own page, without touching what the backend still allows.
  excludeContentKinds?: string[];
  // Content types to hide entirely (CIN-136): the Посты page excludes
  // "video" now that video lives in the studio (/video). Accounts
  // whose platform can publish nothing but excluded types (TikTok is
  // video-only) drop out of the target picker with it.
  excludeContentTypes?: GenerationContentType[];
  topicLabel?: string;
  topicPlaceholder?: string;
  // CIN-130: hides "Куда опубликовать" entirely -- for content that
  // isn't meant to be posted anywhere (a video script). Target
  // accounts are optional at generation time regardless (a connected
  // account is only required at actual publish time, ReviewAndPublish),
  // so hiding the picker here just means the user never has to think
  // about "publishing" for this kind of content at all.
  hideTargetPicker?: boolean;
  // "download" skips the publish step after generation entirely and
  // shows an edit-then-download-as-.txt step instead (ReviewAndDownload).
  postGenerationAction?: "publish" | "download";
}

export function GenerationForm({
  heading,
  subtitle,
  lockedContentType,
  lockedContentKind,
  excludeContentKinds,
  excludeContentTypes,
  topicLabel = "Запрос",
  topicPlaceholder = "например, осенняя коллекция кофе",
  hideTargetPicker = false,
  postGenerationAction = "publish",
}: GenerationFormProps) {
  const { token } = useAuth();
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [targetAccountIds, setTargetAccountIds] = useState<string[]>([]);
  const [topic, setTopic] = useState("");
  const [contentType, setContentType] = useState<GenerationContentType>(lockedContentType ?? "text");
  const [contentKind, setContentKind] = useState(lockedContentKind ?? "post");
  const [brandGuide, setBrandGuide] = useState("");
  const [tone, setTone] = useState("");
  // CIN-143: image templates come from the backend catalog -- the
  // frontend renders whatever GET /content/image-templates returns.
  const [imageTemplates, setImageTemplates] = useState<ImageTemplate[]>([]);
  const [imageTemplate, setImageTemplate] = useState("");
  const [attachments, setAttachments] = useState<NamedAttachment[]>([]);
  const [uploadingAttachment, setUploadingAttachment] = useState(false);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    api.get<SocialAccount[]>("/social-accounts", token).then(setAccounts);
  }, [token]);

  // The dashboard's "Что создадим сегодня?" box hands over its idea as
  // ?topic= (and ?format=image for the picture chip). Read once: a ref
  // guards against the effect re-running with a fresh excludeContentTypes
  // array each render and overwriting what the user has typed since.
  const prefilled = useRef(false);
  useEffect(() => {
    if (prefilled.current) return;
    prefilled.current = true;
    const params = new URLSearchParams(window.location.search);
    const idea = params.get("topic");
    if (idea) setTopic(idea.slice(0, 5000));
    if (params.get("format") === "image" && !lockedContentType && !excludeContentTypes?.includes("image")) {
      setContentType("image");
    }
  }, [lockedContentType, excludeContentTypes]);

  useEffect(() => {
    // Best-effort: if the catalog fails to load, the select simply
    // stays hidden and generation works template-less as before.
    api.get<ImageTemplate[]>("/content/image-templates", token).then(setImageTemplates).catch(() => {});
  }, [token]);

  // Only accounts that can actually publish the locked content type
  // are offered -- e.g. Instagram never appears on the "Сценарий
  // видео" page, since it doesn't support content_type=text at all.
  const selectableAccounts = useMemo(() => {
    if (lockedContentType) {
      return accounts.filter((a) => allowedContentTypesFor([a.platform]).includes(lockedContentType));
    }
    if (excludeContentTypes?.length) {
      return accounts.filter((a) =>
        allowedContentTypesFor([a.platform]).some((ct) => !excludeContentTypes.includes(ct))
      );
    }
    return accounts;
  }, [accounts, lockedContentType, excludeContentTypes]);

  function toggleTargetAccount(accountId: string, checked: boolean) {
    const next = checked ? [...targetAccountIds, accountId] : targetAccountIds.filter((id) => id !== accountId);
    setTargetAccountIds(next);

    if (lockedContentType && lockedContentKind) return;

    const nextPlatforms = platformsFor(next, accounts);
    if (nextPlatforms.length === 0) return; // back to unconstrained -- nothing to reconcile

    const allowedTypes = allowedContentTypesFor(nextPlatforms).filter(
      (ct) => !excludeContentTypes?.includes(ct)
    );
    const nextContentType = allowedTypes.length > 0 && !allowedTypes.includes(contentType) ? allowedTypes[0] : contentType;
    if (nextContentType !== contentType) setContentType(nextContentType);

    const allowedKinds = allowedContentKindsFor(nextPlatforms, nextContentType);
    if (!allowedKinds.includes(contentKind)) setContentKind(allowedKinds[0] ?? "post");
  }

  // Nothing constrains the choice yet when no target account is picked
  // (now optional -- a connected account is only required at actual
  // publish time, ReviewAndPublish) -- fall back to every kind that
  // exists for this content_type on ANY platform, same union
  // publish_matrix.py itself draws from.
  const selectedPlatforms = platformsFor(targetAccountIds, accounts);
  const allowedContentTypes = (
    selectedPlatforms.length > 0 ? allowedContentTypesFor(selectedPlatforms) : ALL_CONTENT_TYPES
  ).filter((ct) => !excludeContentTypes?.includes(ct));
  const allowedContentKinds = (
    selectedPlatforms.length > 0
      ? allowedContentKindsFor(selectedPlatforms, contentType)
      : ALL_CONTENT_KINDS_BY_TYPE[contentType]
  ).filter((kind) => !excludeContentKinds?.includes(kind));

  async function handleAttachmentChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = ""; // allow re-selecting the same file later
    if (!file) return;
    setAttachmentError(null);
    if (attachments.length >= MAX_TOTAL_ATTACHMENTS) {
      setAttachmentError(`Максимум ${MAX_TOTAL_ATTACHMENTS} вложений за генерацию`);
      return;
    }
    setUploadingAttachment(true);
    try {
      const uploaded = await api.upload<Attachment>("/content/attachment", file, token);
      if (attachmentCapReached(attachments, uploaded.attachment_type)) {
        setAttachmentError(
          `Достигнут лимит для типа «${ATTACHMENT_TYPE_LABELS[uploaded.attachment_type]}»`
        );
        return;
      }
      setAttachments((prev) => [...prev, { ...uploaded, name: file.name }]);
    } catch (err) {
      setAttachmentError(err instanceof ApiError ? err.message : "Не удалось загрузить файл");
    } finally {
      setUploadingAttachment(false);
    }
  }

  function removeAttachment(index: number) {
    setAttachments((prev) => prev.filter((_, i) => i !== index));
    setAttachmentError(null);
  }

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  function pollJob(jobId: string) {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const updated = await api.get<GenerationJob>(`/content/${jobId}`, token);
        setJob(updated);
        if (TERMINAL_STATUSES.has(updated.status) && pollRef.current) {
          clearInterval(pollRef.current);
        }
      } catch {
        if (pollRef.current) clearInterval(pollRef.current);
      }
    }, POLL_INTERVAL_MS);
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setJob(null);
    setSubmitting(true);
    try {
      const created = await api.post<GenerationJob>(
        "/content/generate",
        {
          topic,
          target_account_ids: targetAccountIds,
          content_type: contentType,
          content_kind: contentKind,
          brand_guide: brandGuide || null,
          tone: tone || null,
          image_template: contentType === "image" && imageTemplate ? imageTemplate : null,
          attachments: attachments.map((a) => ({ url: a.url, attachment_type: a.attachment_type })),
        },
        token
      );
      setJob(created);
      if (!TERMINAL_STATUSES.has(created.status)) pollJob(created.id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        setError("Лимит генераций по тарифу исчерпан. Обновите тариф на странице «Тариф».");
      } else {
        setError(err instanceof ApiError ? err.message : "Не удалось запустить генерацию");
      }
    } finally {
      setSubmitting(false);
    }
  }

  // What the preview card pretends to be before there is a result: the
  // first picked account's platform, else Telegram (every plan has it).
  const previewPlatform = selectedPlatforms[0] ?? "telegram";
  const previewAccount = accounts.find((a) => a.id === targetAccountIds[0]);
  const jobDone =
    job?.status === "completed" &&
    (job.output_payload?.text || job.output_payload?.image_url || job.output_payload?.video_url);
  const selectedTemplate = imageTemplates.find((t) => t.id === imageTemplate);

  return (
    <>
      <div className="page-header">
        <div>
          <h1>{heading}</h1>
          <p className="muted">{subtitle}</p>
        </div>
      </div>
      <div className="studio">
        <form onSubmit={handleSubmit} className="panel studio-form">
          <h2>Ваша идея</h2>
          {/* Optional -- a connected account is only required at actual
              publish time (ReviewAndPublish), not to generate. Narrows
              Формат/Тип контента to what the picked account(s) can
              actually publish when used, same as before; skipped
              entirely when there's nothing to pick from yet. */}
          {!hideTargetPicker && selectableAccounts.length > 0 && (
            <fieldset className="field-block plain">
              <legend className="field-label">Куда опубликовать (необязательно)</legend>
              <div className="chip-group">
                {selectableAccounts.map((a) => (
                  <label key={a.id}>
                    <input
                      type="checkbox"
                      checked={targetAccountIds.includes(a.id)}
                      onChange={(e) => toggleTargetAccount(a.id, e.target.checked)}
                    />
                    {platformLabel(a.platform)} — {a.display_name ?? a.external_account_id}
                  </label>
                ))}
              </div>
            </fieldset>
          )}
          {!lockedContentType && allowedContentTypes.length > 1 && (
            <ChipSelect
              label="Формат"
              value={contentType}
              options={allowedContentTypes.map((value) => ({ value, label: CONTENT_TYPE_LABELS[value] }))}
              onChange={(nextContentType) => {
                setContentType(nextContentType);
                const available = (
                  selectedPlatforms.length > 0
                    ? allowedContentKindsFor(selectedPlatforms, nextContentType)
                    : ALL_CONTENT_KINDS_BY_TYPE[nextContentType]
                ).filter((kind) => !excludeContentKinds?.includes(kind));
                if (!available.includes(contentKind)) setContentKind(available[0] ?? "post");
              }}
            />
          )}
          {!lockedContentKind && allowedContentKinds.length > 1 && (
            <ChipSelect
              label="Тип контента"
              value={contentKind}
              options={allowedContentKinds.map((value) => ({ value, label: CONTENT_KIND_LABELS[value] ?? value }))}
              onChange={setContentKind}
            />
          )}
          <label className="field-block">
            <span className="field-label">{topicLabel}</span>
            <textarea
              required
              rows={6}
              maxLength={5000}
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder={topicPlaceholder}
            />
          </label>
          <ChipSelect label="Тон" value={tone} options={TONE_OPTIONS} onChange={setTone} />
          {contentType === "image" && imageTemplates.length > 0 && (
            <div className="field-block" role="group" aria-label="Шаблон изображения">
              <span className="field-label">Шаблон изображения (необязательно)</span>
              <div className="template-grid">
                <button
                  type="button"
                  className={`template-pick${imageTemplate === "" ? " selected" : ""}`}
                  aria-pressed={imageTemplate === ""}
                  onClick={() => setImageTemplate("")}
                >
                  <span className="template-pick-art default" aria-hidden="true" />
                  <b>По умолчанию</b>
                </button>
                {imageTemplates.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    className={`template-pick${imageTemplate === t.id ? " selected" : ""}`}
                    aria-pressed={imageTemplate === t.id}
                    title={t.description}
                    onClick={() => setImageTemplate(t.id)}
                  >
                    {t.preview_url ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={t.preview_url} alt="" className="template-pick-art" />
                    ) : (
                      <span className="template-pick-art" aria-hidden="true" />
                    )}
                    <b>{t.title}</b>
                  </button>
                ))}
              </div>
              {selectedTemplate && (
                <p className="muted hint">{selectedTemplate.description}</p>
              )}
            </div>
          )}
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
          <label className="drop">
            <Paperclip size={16} aria-hidden="true" /> Прикрепить файлы
            <small>до {MAX_TOTAL_ATTACHMENTS}: видео и аудио — не больше 1 каждого</small>
            <input
              type="file"
              accept={ATTACHMENT_ACCEPT}
              onChange={handleAttachmentChange}
              disabled={uploadingAttachment || attachments.length >= MAX_TOTAL_ATTACHMENTS}
            />
          </label>
          {uploadingAttachment && <p className="muted">Загружаем файл…</p>}
          {attachmentError && <p className="error">{attachmentError}</p>}
          {attachments.length > 0 && (
            <ul className="attachment-list">
              {attachments.map((a, index) => (
                <li key={`${a.url}-${index}`} className="muted">
                  {a.name} ({ATTACHMENT_TYPE_LABELS[a.attachment_type]}){" "}
                  <button type="button" className="secondary" onClick={() => removeAttachment(index)}>
                    Убрать
                  </button>
                </li>
              ))}
            </ul>
          )}
          {error && <p className="error">{error}</p>}
          <button type="submit" disabled={submitting || uploadingAttachment}>
            <Sparkles size={16} aria-hidden="true" /> {submitting ? "Запускаем…" : "Сгенерировать"}
          </button>
        </form>

        <section className="panel studio-preview" aria-live="polite">
          <div className="section-head">
            <h2>Предпросмотр</h2>
            {job ? (
              <span className={`badge ${job.status}`}>{JOB_STATUS_LABELS[job.status] ?? job.status}</span>
            ) : (
              <span className="tag purple">Пример</span>
            )}
          </div>
          {jobDone && job ? (
            postGenerationAction === "download" ? (
              <ReviewAndDownload job={job} initialCaption={topic} />
            ) : (
              <ReviewAndPublish
                imageUrl={job.output_payload?.image_url}
                videoUrl={job.output_payload?.video_url}
                generatedText={job.output_payload?.text}
                generationJobId={job.id}
                contentKind={contentKind}
                initialCaption={topic}
                accounts={accounts}
                targetAccountIds={targetAccountIds}
              />
            )
          ) : (
            <>
              <PostCard
                platform={previewPlatform}
                author={previewAccount?.display_name ?? previewAccount?.external_account_id ?? "Ваш аккаунт"}
                media={
                  contentType !== "text" && (
                    <Poster title={topic.trim().slice(0, 48) || "Хорошие идеи зажигают."} tone="violet" />
                  )
                }
              >
                <p className="post-caption placeholder">
                  {job?.status === "queued" || job?.status === "processing"
                    ? "Генерируем… можно продолжать работу — результат появится здесь."
                    : topic.trim() || "Здесь появится ваш пост. Опишите идею слева и нажмите «Сгенерировать»."}
                </p>
              </PostCard>
              {(job?.status === "queued" || job?.status === "processing") && (
                <div className="progress" aria-hidden="true">
                  <span className="indeterminate" />
                </div>
              )}
              {job?.status === "failed" && <p className="error">{job.error_message}</p>}
              {job?.status === "flagged" && <p className="error">Отклонено модерацией: {job.error_message}</p>}
            </>
          )}
        </section>
      </div>
    </>
  );
}
