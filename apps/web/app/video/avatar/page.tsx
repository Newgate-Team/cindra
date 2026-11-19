"use client";

import { Sparkles, UserRound } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type {
  HeyGenAvatar,
  HeyGenVideoJob,
  HeyGenVoice,
  Integration,
  Page,
  SocialAccount,
  TokenPage,
} from "@/lib/types";

import { RequireAuth } from "../../components/RequireAuth";
import { ChipSelect } from "../../components/Studio";
import { ReviewAndPublish } from "../../components/ReviewAndPublish";
import { SaveToDriveButton } from "../../components/SaveToDriveButton";

const MAX_VARIANTS = 4;
const POLL_MS = 10_000;

const ASPECT_RATIOS = [
  { value: "9:16", label: "9:16 · Reels, Shorts, TikTok" },
  { value: "16:9", label: "16:9 · YouTube" },
  { value: "1:1", label: "1:1 · лента" },
];

const VOICE_LANGUAGES = ["Russian", "English"];

const STATUS_LABELS: Record<HeyGenVideoJob["status"], string> = {
  queued: "в очереди",
  processing: "рендерится",
  completed: "готово",
  failed: "ошибка",
  flagged: "отклонено",
};

type Ownership = "private" | "public";

// HeyGen look training states (private avatars only) that can't
// render yet -- HeyGen would answer 409 resource_not_ready.
const UNUSABLE_STATUS_LABELS: Record<string, string> = {
  processing: "обучается",
  pending_consent: "ждёт согласия",
  failed: "ошибка обучения",
};

function isUsable(avatar: HeyGenAvatar): boolean {
  return avatar.status === null || avatar.status === "completed";
}

function AvatarPicker({
  selected,
  onToggle,
  onLoaded,
}: {
  selected: string[];
  onToggle: (avatar: HeyGenAvatar) => void;
  onLoaded: (avatars: HeyGenAvatar[]) => void;
}) {
  const { token } = useAuth();
  const [ownership, setOwnership] = useState<Ownership>("private");
  const [avatars, setAvatars] = useState<HeyGenAvatar[]>([]);
  const [nextToken, setNextToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (pageToken: string | null, replace: boolean) => {
      setLoading(true);
      setError(null);
      const params = new URLSearchParams({ ownership });
      if (pageToken) params.set("token", pageToken);
      try {
        const page = await api.get<TokenPage<HeyGenAvatar>>(
          `/integrations/heygen/avatars?${params}`,
          token
        );
        setAvatars((current) => (replace ? page.items : [...current, ...page.items]));
        setNextToken(page.next_token);
        onLoaded(page.items);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Не удалось загрузить аватары");
      } finally {
        setLoading(false);
      }
    },
    [ownership, token, onLoaded]
  );

  useEffect(() => {
    load(null, true);
  }, [load]);

  return (
    <>
      <div className="chip-row" role="group" aria-label="Чьи аватары показывать">
        <button
          type="button"
          className={`chip${ownership === "private" ? " selected" : ""}`}
          aria-pressed={ownership === "private"}
          onClick={() => setOwnership("private")}
        >
          Мои
        </button>
        <button
          type="button"
          className={`chip${ownership === "public" ? " selected" : ""}`}
          aria-pressed={ownership === "public"}
          onClick={() => setOwnership("public")}
        >
          Публичные HeyGen
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {!loading && !error && avatars.length === 0 && (
        <p className="muted">
          {ownership === "private"
            ? "Своих аватаров в HeyGen пока нет — выберите публичный или создайте свой в HeyGen."
            : "HeyGen не вернул публичных аватаров."}
        </p>
      )}
      <div className="avatar-grid">
        {avatars.map((avatar) => {
          const isSelected = selected.includes(avatar.id);
          const usable = isUsable(avatar);
          return (
            <button
              key={avatar.id}
              type="button"
              className={`avatar-tile${isSelected ? " selected" : ""}`}
              aria-pressed={isSelected}
              disabled={!usable}
              title={usable ? avatar.name : `${avatar.name} — ${UNUSABLE_STATUS_LABELS[avatar.status ?? ""] ?? avatar.status}`}
              onClick={() => onToggle(avatar)}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={avatar.preview_image_url ?? ""} alt="" />
              <span>
                {avatar.name}
                {!usable && ` · ${UNUSABLE_STATUS_LABELS[avatar.status ?? ""] ?? avatar.status}`}
              </span>
            </button>
          );
        })}
      </div>
      {nextToken && (
        <button type="button" className="secondary" disabled={loading} onClick={() => load(nextToken, false)}>
          {loading ? "Загружаем…" : "Показать ещё"}
        </button>
      )}
    </>
  );
}

function VoicePicker({ value, onChange }: { value: string; onChange: (voiceId: string) => void }) {
  const { token } = useAuth();
  // HeyGen lists only public voices unless asked for "private" -- a
  // user's own cloned voice lives there.
  const [voiceType, setVoiceType] = useState<Ownership>("private");
  const [language, setLanguage] = useState(VOICE_LANGUAGES[0]);
  const [voices, setVoices] = useState<HeyGenVoice[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    const params = new URLSearchParams({ type: voiceType });
    if (voiceType === "public") params.set("language", language);
    api
      .get<TokenPage<HeyGenVoice>>(`/integrations/heygen/voices?${params}`, token)
      .then((page) => setVoices(page.items))
      .catch((err) => setError(err instanceof ApiError ? err.message : "Не удалось загрузить голоса"));
  }, [voiceType, language, token]);

  return (
    <>
      <label className="field-block">
        <span className="field-label">Голоса</span>
        <select
          value={voiceType}
          onChange={(e) => {
            setVoiceType(e.target.value as Ownership);
            onChange("");
          }}
        >
          <option value="private">Мои голоса (клоны в HeyGen)</option>
          <option value="public">Публичные голоса HeyGen</option>
        </select>
      </label>
      {voiceType === "public" && (
        <label className="field-block">
          <span className="field-label">Язык голоса</span>
          <select value={language} onChange={(e) => setLanguage(e.target.value)}>
            {VOICE_LANGUAGES.map((lang) => (
              <option key={lang} value={lang}>
                {lang === "Russian" ? "Русский" : "Английский"}
              </option>
            ))}
          </select>
        </label>
      )}
      <label className="field-block">
        <span className="field-label">Голос</span>
        <select value={value} onChange={(e) => onChange(e.target.value)}>
          <option value="">Голос аватара по умолчанию</option>
          {voices.map((voice) => (
            <option key={voice.voice_id} value={voice.voice_id}>
              {voice.name}
              {voice.gender ? ` (${voice.gender === "female" ? "жен." : "муж."})` : ""}
            </option>
          ))}
        </select>
      </label>
      {error && <p className="error">{error}</p>}
    </>
  );
}

function VariantCard({
  job,
  avatarName,
  accounts,
}: {
  job: HeyGenVideoJob;
  avatarName: string;
  accounts: SocialAccount[];
}) {
  const [publishing, setPublishing] = useState(false);

  return (
    <div className="card">
      <p>
        <strong>{avatarName}</strong> <span className={`badge ${job.status}`}>{STATUS_LABELS[job.status]}</span>
      </p>
      {/* ReviewAndPublish renders its own preview of the same video. */}
      {job.video_url && !publishing && (
        <video src={job.video_url} controls playsInline preload="metadata" />
      )}
      {job.error_message && <p className="error">{job.error_message}</p>}
      {job.status === "completed" && job.video_url && !publishing && (
        <div className="list-row-actions">
          <button type="button" onClick={() => setPublishing(true)}>
            Опубликовать этот вариант
          </button>
          <Link
            className="secondary"
            href={`/video/montage?${new URLSearchParams({ video: job.video_url, name: avatarName })}`}
          >
            Смонтировать
          </Link>
          <SaveToDriveButton url={job.video_url} title={avatarName} />
        </div>
      )}
      {publishing && job.video_url && (
        <ReviewAndPublish
          videoUrl={job.video_url}
          generationJobId={job.id}
          contentKind="post"
          initialCaption={job.script}
          accounts={accounts}
          targetAccountIds={[]}
        />
      )}
    </div>
  );
}

function AvatarVideoStudio({ integration }: { integration: Integration }) {
  const { token } = useAuth();
  const [script, setScript] = useState("");
  const [aspectRatio, setAspectRatio] = useState(ASPECT_RATIOS[0].value);
  const [selected, setSelected] = useState<HeyGenAvatar[]>([]);
  const [voiceOverride, setVoiceOverride] = useState("");
  const [knownAvatars, setKnownAvatars] = useState<Record<string, HeyGenAvatar>>({});
  const [jobs, setJobs] = useState<HeyGenVideoJob[] | null>(null);
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const reloadJobs = useCallback(() => {
    api
      .get<Page<HeyGenVideoJob>>("/integrations/heygen/videos?limit=40", token)
      .then((page) => setJobs(page.items))
      .catch(() => setJobs((current) => current ?? []));
  }, [token]);

  useEffect(reloadJobs, [reloadJobs]);
  useEffect(() => {
    api.get<SocialAccount[]>("/social-accounts", token).then(setAccounts).catch(() => setAccounts([]));
  }, [token]);

  const inFlight = jobs?.some((job) => job.status === "queued" || job.status === "processing") ?? false;
  useEffect(() => {
    if (!inFlight) return;
    const timer = setInterval(reloadJobs, POLL_MS);
    return () => clearInterval(timer);
  }, [inFlight, reloadJobs]);

  const rememberAvatars = useCallback((avatars: HeyGenAvatar[]) => {
    setKnownAvatars((current) => {
      const next = { ...current };
      for (const avatar of avatars) next[avatar.id] = avatar;
      return next;
    });
  }, []);

  function toggleAvatar(avatar: HeyGenAvatar) {
    setSelected((current) => {
      if (current.some((a) => a.id === avatar.id)) return current.filter((a) => a.id !== avatar.id);
      if (current.length >= MAX_VARIANTS) return current;
      return [...current, avatar];
    });
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const created = await api.post<HeyGenVideoJob[]>(
        "/integrations/heygen/videos",
        {
          script,
          aspect_ratio: aspectRatio,
          // No voice_id = HeyGen speaks in the avatar's own default voice.
          variants: selected.map((avatar) => ({
            avatar_id: avatar.id,
            ...(voiceOverride ? { voice_id: voiceOverride } : {}),
          })),
        },
        token
      );
      setJobs((current) => [...created, ...(current ?? [])]);
      setSelected([]);
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        setError(`${err.message}. Обновите тариф на странице «Тариф».`);
      } else {
        setError(err instanceof ApiError ? err.message : "Не удалось запустить генерацию");
      }
    } finally {
      setSubmitting(false);
    }
  }

  const batches = useMemo(() => {
    const grouped = new Map<string, HeyGenVideoJob[]>();
    for (const job of jobs ?? []) {
      grouped.set(job.batch_id, [...(grouped.get(job.batch_id) ?? []), job]);
    }
    return [...grouped.values()];
  }, [jobs]);

  return (
    <>
      {!integration.is_own && (
        <p className="muted">
          Используется HeyGen участника команды ({integration.account_label}) — рендеры оплачиваются
          с его кошелька.
        </p>
      )}
      <div className="studio">
        <form onSubmit={handleSubmit} className="panel studio-form">
          <h2>Выберите аватара</h2>
          <p className="muted hint">
            Каждый выбранный станет отдельным вариантом ролика (до {MAX_VARIANTS}).
            {selected.length > 0 && <strong> Выбрано: {selected.length}</strong>}
          </p>
          <AvatarPicker
            selected={selected.map((a) => a.id)}
            onToggle={toggleAvatar}
            onLoaded={rememberAvatars}
          />
          <VoicePicker value={voiceOverride} onChange={setVoiceOverride} />
          <ChipSelect
            label="Формат"
            value={aspectRatio}
            options={ASPECT_RATIOS}
            onChange={setAspectRatio}
          />
          <label className="field-block">
            <span className="field-label">Сценарий — что скажет аватар</span>
            <textarea
              required
              rows={6}
              maxLength={5000}
              value={script}
              onChange={(e) => setScript(e.target.value)}
              placeholder="Текст, который аватар произнесёт в кадре"
            />
          </label>
          <p className="muted hint">
            Каждый вариант — отдельный рендер, оплачивается с кошелька HeyGen API.
          </p>
          {error && <p className="error">{error}</p>}
          <button type="submit" disabled={submitting || selected.length === 0 || !script.trim()}>
            <Sparkles size={16} aria-hidden="true" />{" "}
            {submitting
              ? "Запускаем…"
              : selected.length > 1
                ? `Сгенерировать ${selected.length} варианта`
                : "Сгенерировать видео"}
          </button>
        </form>

        <section className="panel studio-preview">
          <div className="section-head">
            <h2>Ваши аватар-видео</h2>
            {inFlight && <span className="badge processing">рендерится</span>}
          </div>
          {jobs === null && (
            <div className="skeleton" aria-busy="true" />
          )}
          {jobs !== null && jobs.length === 0 && (
            <div className="empty">
              <span className="tile" aria-hidden="true">
                <UserRound size={26} />
              </span>
              <h3>Пока ничего не сгенерировано</h3>
              <p className="muted">Выберите аватара, напишите сценарий — готовые варианты появятся здесь.</p>
            </div>
          )}
          {batches.map((batch) => (
            <div key={batch[0].batch_id} className="batch">
              <p className="muted hint">
                {new Date(batch[0].created_at).toLocaleString("ru-RU")} ·{" "}
                {batch[0].script.length > 120 ? `${batch[0].script.slice(0, 120)}…` : batch[0].script}
              </p>
              <div className="variant-grid">
                {batch.map((job) => (
                  <VariantCard
                    key={job.id}
                    job={job}
                    avatarName={knownAvatars[job.avatar_id]?.name ?? "Аватар"}
                    accounts={accounts}
                  />
                ))}
              </div>
            </div>
          ))}
        </section>
      </div>
    </>
  );
}

function AvatarVideoPage() {
  const { token } = useAuth();
  const [integration, setIntegration] = useState<Integration | null | undefined>(undefined);

  useEffect(() => {
    api
      .get<Integration[]>("/integrations", token)
      .then((list) => setIntegration(list.find((item) => item.provider === "heygen") ?? null))
      .catch(() => setIntegration(null));
  }, [token]);

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Аватар-видео</h1>
          <p className="muted">Ваш ведущий для каждой истории: несколько вариантов ролика сразу, лучший — в соцсети.</p>
        </div>
      </div>
      {integration === undefined && <div className="skeleton" aria-busy="true" />}
      {integration === null && (
        <section className="panel empty">
          <span className="tile" aria-hidden="true">
            <UserRound size={26} />
          </span>
          <h2>Сначала подключите HeyGen</h2>
          <p className="muted">Видео рендерится на вашем аккаунте HeyGen — ключ добавляется в «Инструментах».</p>
          <Link className="button-link" href="/integrations">
            Перейти в «Инструменты»
          </Link>
        </section>
      )}
      {integration && <AvatarVideoStudio integration={integration} />}
    </>
  );
}

export default function AvatarVideoRoute() {
  return (
    <RequireAuth>
      <AvatarVideoPage />
    </RequireAuth>
  );
}
