"use client";

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
import { ReviewAndPublish } from "../../components/ReviewAndPublish";

const MAX_VARIANTS = 4;
const POLL_MS = 10_000;

const ASPECT_RATIOS = [
  { value: "9:16", label: "9:16 — Reels, Shorts, TikTok" },
  { value: "16:9", label: "16:9 — YouTube" },
  { value: "1:1", label: "1:1 — лента" },
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
      <div className="tab-row">
        <button
          type="button"
          className={ownership === "private" ? undefined : "secondary"}
          onClick={() => setOwnership("private")}
        >
          Мои аватары
        </button>
        <button
          type="button"
          className={ownership === "public" ? undefined : "secondary"}
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
          return (
            <button
              key={avatar.id}
              type="button"
              className={`avatar-tile${isSelected ? " selected" : ""}`}
              aria-pressed={isSelected}
              onClick={() => onToggle(avatar)}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={avatar.preview_image_url ?? ""} alt="" />
              <span title={avatar.name}>{avatar.name}</span>
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
  const [language, setLanguage] = useState(VOICE_LANGUAGES[0]);
  const [voices, setVoices] = useState<HeyGenVoice[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    api
      .get<TokenPage<HeyGenVoice>>(`/integrations/heygen/voices?language=${language}`, token)
      .then((page) => setVoices(page.items))
      .catch((err) => setError(err instanceof ApiError ? err.message : "Не удалось загрузить голоса"));
  }, [language, token]);

  return (
    <>
      <label>
        Язык голоса
        <select value={language} onChange={(e) => setLanguage(e.target.value)}>
          {VOICE_LANGUAGES.map((lang) => (
            <option key={lang} value={lang}>
              {lang === "Russian" ? "Русский" : "Английский"}
            </option>
          ))}
        </select>
      </label>
      <label>
        Голос
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
        <button type="button" onClick={() => setPublishing(true)}>
          Опубликовать этот вариант
        </button>
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

  const missingVoice = !voiceOverride && selected.some((avatar) => !avatar.default_voice_id);

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
          variants: selected.map((avatar) => ({
            avatar_id: avatar.id,
            voice_id: voiceOverride || avatar.default_voice_id,
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
      <form onSubmit={handleSubmit} className="card">
        <label>
          Сценарий — что скажет аватар
          <textarea
            required
            rows={6}
            maxLength={5000}
            value={script}
            onChange={(e) => setScript(e.target.value)}
            placeholder="Текст, который аватар произнесёт в кадре"
          />
        </label>
        <label>
          Формат
          <select value={aspectRatio} onChange={(e) => setAspectRatio(e.target.value)}>
            {ASPECT_RATIOS.map((ratio) => (
              <option key={ratio.value} value={ratio.value}>
                {ratio.label}
              </option>
            ))}
          </select>
        </label>

        <p>
          Аватары — каждый выбранный станет отдельным вариантом ролика (до {MAX_VARIANTS}).
          {selected.length > 0 && <span className="muted"> Выбрано: {selected.length}</span>}
        </p>
        <AvatarPicker
          selected={selected.map((a) => a.id)}
          onToggle={toggleAvatar}
          onLoaded={rememberAvatars}
        />
        <VoicePicker value={voiceOverride} onChange={setVoiceOverride} />

        {missingVoice && (
          <p className="error">
            У части выбранных аватаров нет голоса по умолчанию — выберите голос выше.
          </p>
        )}
        <p className="muted">
          Каждый вариант — отдельный рендер, оплачивается с кошелька HeyGen API.
        </p>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={submitting || selected.length === 0 || missingVoice || !script.trim()}>
          {submitting
            ? "Запускаем…"
            : selected.length > 1
              ? `Сгенерировать ${selected.length} варианта`
              : "Сгенерировать видео"}
        </button>
      </form>

      <h2>Ваши аватар-видео</h2>
      {jobs === null && <p className="muted">Загрузка…</p>}
      {jobs !== null && jobs.length === 0 && <p className="muted">Пока ничего не сгенерировано.</p>}
      {batches.map((batch) => (
        <div key={batch[0].batch_id}>
          <p className="muted">
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
          <p className="muted">
            Ролик с AI-аватаром HeyGen по вашему сценарию — сразу в нескольких вариантах, лучший
            публикуется в подключённые соцсети.
          </p>
        </div>
      </div>
      {integration === undefined && <p className="muted">Загрузка…</p>}
      {integration === null && (
        <div className="card">
          <p>Сначала подключите свой аккаунт HeyGen.</p>
          <Link href="/integrations">Перейти в «Инструменты» →</Link>
        </div>
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
