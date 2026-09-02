"use client";

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

import { ReviewAndPublish } from "./ReviewAndPublish";

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
  emptyAccountsMessage?: string;
  // CIN-130: hides "Куда опубликовать" entirely -- for content that
  // isn't meant to be posted anywhere (a video script). Target
  // accounts are still sent to /content/generate under the hood
  // (every eligible one, silently) since the backend still requires
  // >=1 for its content_type/content_kind validation and tone
  // guidance, but the user never has to think about "publishing".
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
  emptyAccountsMessage = "Чтобы начать генерацию, сначала подключите соцсеть на странице «Соцсети».",
  hideTargetPicker = false,
  postGenerationAction = "publish",
}: GenerationFormProps) {
  const { token } = useAuth();
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [accountsLoaded, setAccountsLoaded] = useState(false);
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
    api.get<SocialAccount[]>("/social-accounts", token).then((list) => {
      setAccounts(list);
      setAccountsLoaded(true);
    });
  }, [token]);

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

  // CIN-130: when the picker itself is hidden, every eligible account
  // is sent along silently -- /content/generate still requires >=1
  // for its own validation/tone guidance, even though nothing here
  // ever gets published to any of them.
  useEffect(() => {
    if (hideTargetPicker) {
      setTargetAccountIds(selectableAccounts.map((a) => a.id));
    }
  }, [hideTargetPicker, selectableAccounts]);

  function toggleTargetAccount(accountId: string, checked: boolean) {
    const next = checked ? [...targetAccountIds, accountId] : targetAccountIds.filter((id) => id !== accountId);
    setTargetAccountIds(next);

    if (lockedContentType && lockedContentKind) return;

    const nextPlatforms = platformsFor(next, accounts);
    const allowedTypes = allowedContentTypesFor(nextPlatforms).filter(
      (ct) => !excludeContentTypes?.includes(ct)
    );
    const nextContentType = allowedTypes.length > 0 && !allowedTypes.includes(contentType) ? allowedTypes[0] : contentType;
    if (nextContentType !== contentType) setContentType(nextContentType);

    const allowedKinds = allowedContentKindsFor(nextPlatforms, nextContentType);
    if (!allowedKinds.includes(contentKind)) setContentKind(allowedKinds[0] ?? "post");
  }

  const selectedPlatforms = platformsFor(targetAccountIds, accounts);
  const allowedContentTypes = allowedContentTypesFor(selectedPlatforms).filter(
    (ct) => !excludeContentTypes?.includes(ct)
  );
  const allowedContentKinds = allowedContentKindsFor(selectedPlatforms, contentType).filter(
    (kind) => !excludeContentKinds?.includes(kind)
  );

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

  if (accountsLoaded && selectableAccounts.length === 0) {
    return (
      <>
        <div className="page-header">
          <div>
            <h1>{heading}</h1>
            <p className="muted">{subtitle}</p>
          </div>
        </div>
        <p className="muted">{emptyAccountsMessage}</p>
      </>
    );
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1>{heading}</h1>
          <p className="muted">{subtitle}</p>
        </div>
      </div>
      <form onSubmit={handleSubmit} className="card">
        {!hideTargetPicker && (
          <fieldset className="chip-group">
            <legend>Куда опубликовать</legend>
            {selectableAccounts.map((a) => (
              <label key={a.id}>
                <input
                  type="checkbox"
                  checked={targetAccountIds.includes(a.id)}
                  onChange={(e) => toggleTargetAccount(a.id, e.target.checked)}
                />
                {a.platform} — {a.display_name ?? a.external_account_id}
              </label>
            ))}
          </fieldset>
        )}
        <label>
          {topicLabel}
          <textarea
            required
            rows={6}
            maxLength={5000}
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder={topicPlaceholder}
          />
        </label>
        {!lockedContentType && (
          <label>
            Формат
            <select
              value={contentType}
              disabled={targetAccountIds.length === 0}
              onChange={(e) => {
                const nextContentType = e.target.value as GenerationContentType;
                setContentType(nextContentType);
                const available = allowedContentKindsFor(selectedPlatforms, nextContentType).filter(
                  (kind) => !excludeContentKinds?.includes(kind)
                );
                if (!available.includes(contentKind)) setContentKind(available[0] ?? "post");
              }}
            >
              {allowedContentTypes.map((value) => (
                <option key={value} value={value}>
                  {CONTENT_TYPE_LABELS[value]}
                </option>
              ))}
            </select>
          </label>
        )}
        {!lockedContentKind && (
          <label>
            Тип контента
            <select
              value={contentKind}
              disabled={targetAccountIds.length === 0}
              onChange={(e) => setContentKind(e.target.value)}
            >
              {allowedContentKinds.map((value) => (
                <option key={value} value={value}>
                  {CONTENT_KIND_LABELS[value] ?? value}
                </option>
              ))}
            </select>
          </label>
        )}
        {contentType === "image" && imageTemplates.length > 0 && (
          <label>
            Шаблон (необязательно)
            <select value={imageTemplate} onChange={(e) => setImageTemplate(e.target.value)}>
              <option value="">По умолчанию</option>
              {imageTemplates.map((t) => (
                <option key={t.id} value={t.id} title={t.description}>
                  {t.title}
                </option>
              ))}
            </select>
          </label>
        )}
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
            rows={3}
            value={brandGuide}
            onChange={(e) => setBrandGuide(e.target.value)}
            placeholder="тон и стиль, которых нужно придерживаться"
          />
        </label>
        <label>
          Прикрепить файлы (необязательно, до {MAX_TOTAL_ATTACHMENTS}: видео и аудио — не больше 1 каждого)
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
          <ul>
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
        <button type="submit" disabled={submitting || uploadingAttachment || targetAccountIds.length === 0}>
          {submitting ? "Запускаем…" : "Сгенерировать"}
        </button>
      </form>

      {job && (
        <div className="card" style={{ marginTop: 24 }}>
          <p>
            Статус генерации: <span className={`badge ${job.status}`}>{job.status}</span>
          </p>
          {job.status === "completed" &&
            (job.output_payload?.text || job.output_payload?.image_url || job.output_payload?.video_url) &&
            (postGenerationAction === "download" ? (
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
            ))}
          {job.status === "failed" && <p className="error">{job.error_message}</p>}
          {job.status === "flagged" && (
            <p className="error">Отклонено модерацией: {job.error_message}</p>
          )}
          {(job.status === "queued" || job.status === "processing") && (
            <p className="muted">Генерируем…</p>
          )}
        </div>
      )}
    </>
  );
}
