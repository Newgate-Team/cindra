"use client";

import { ChevronDown, ChevronUp, Film, Image as ImageIcon, Music, Scissors, Upload, X } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState, type ChangeEvent, type FormEvent } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type {
  HeyGenVideoJob,
  MontageAgentsStatus,
  MontageJob,
  Page,
  SocialAccount,
} from "@/lib/types";

import { RequireAuth } from "../../components/RequireAuth";
import { ReviewAndPublish } from "../../components/ReviewAndPublish";
import { ChipSelect } from "../../components/Studio";
import { SaveToDriveButton } from "../../components/SaveToDriveButton";

const POLL_MS = 5_000;
const MAX_CLIPS = 20;

interface DraftClip {
  key: string;
  type: "video" | "image";
  url: string;
  name: string;
  duration_seconds: number;
}

const STATUS_LABELS: Record<MontageJob["status"], string> = {
  queued: "в очереди",
  processing: "монтируется",
  completed: "готово",
  failed: "ошибка",
};

function newKey(): string {
  return Math.random().toString(36).slice(2);
}

function DaVinciStatus() {
  const { token } = useAuth();
  const [status, setStatus] = useState<MontageAgentsStatus | null>(null);

  useEffect(() => {
    api.get<MontageAgentsStatus>("/montage/agents/status", token).then(setStatus).catch(() => {});
  }, [token]);

  if (!status) return null;
  return status.online ? (
    <p className="status-line">
      <span className="tag green">DaVinci на связи</span>
      <span className="muted">Монтаж начнётся сразу.</span>
    </p>
  ) : (
    <p className="status-line">
      <span className="tag">DaVinci не на связи</span>
      <span className="muted">Монтаж встанет в очередь и начнётся, когда он подключится.</span>
    </p>
  );
}

function MontageBuilder({ onCreated }: { onCreated: (job: MontageJob) => void }) {
  const { token } = useAuth();
  const searchParams = useSearchParams();
  const [clips, setClips] = useState<DraftClip[]>(() => {
    const video = searchParams.get("video");
    return video
      ? [{ key: newKey(), type: "video", url: video, name: searchParams.get("name") ?? "Видео", duration_seconds: 3 }]
      : [];
  });
  const [avatarVideos, setAvatarVideos] = useState<HeyGenVideoJob[]>([]);
  const [music, setMusic] = useState<{ url: string; name: string } | null>(null);
  const [subtitles, setSubtitles] = useState(true);
  const [language, setLanguage] = useState("auto");
  const [aspectRatio, setAspectRatio] = useState("9:16");
  const [title, setTitle] = useState("");
  const [uploading, setUploading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<Page<HeyGenVideoJob>>("/integrations/heygen/videos?limit=40", token)
      .then((page) => setAvatarVideos(page.items.filter((job) => job.status === "completed" && job.video_url)))
      .catch(() => setAvatarVideos([]));
  }, [token]);

  function addClip(clip: Omit<DraftClip, "key">) {
    setClips((current) => (current.length >= MAX_CLIPS ? current : [...current, { ...clip, key: newKey() }]));
  }

  function move(index: number, delta: number) {
    setClips((current) => {
      const next = [...current];
      const [item] = next.splice(index, 1);
      next.splice(Math.max(0, Math.min(next.length, index + delta)), 0, item);
      return next;
    });
  }

  async function upload(file: File): Promise<{ url: string; type: string } | null> {
    setError(null);
    setUploading(true);
    try {
      return await api.upload<{ url: string; type: string }>("/montage/media", file, token);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить файл");
      return null;
    } finally {
      setUploading(false);
    }
  }

  async function handleClipFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    const stored = await upload(file);
    if (stored && (stored.type === "video" || stored.type === "image")) {
      addClip({ type: stored.type, url: stored.url, name: file.name, duration_seconds: 3 });
    } else if (stored) {
      setError("Это аудио — добавьте его как музыку ниже");
    }
  }

  async function handleMusicFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    const stored = await upload(file);
    if (stored?.type === "audio") setMusic({ url: stored.url, name: file.name });
    else if (stored) setError("Для музыки нужен MP3, WAV или M4A");
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    await create(true);
  }

  async function create(render: boolean) {
    setError(null);
    setSubmitting(true);
    try {
      const job = await api.post<MontageJob>(
        "/montage/jobs",
        {
          clips: clips.map(({ type, url, name, duration_seconds }) => ({
            type,
            url,
            name,
            duration_seconds: type === "image" ? duration_seconds : null,
          })),
          music: music ? { url: music.url } : null,
          subtitles,
          subtitles_language: language,
          aspect_ratio: aspectRatio,
          title: title || null,
          render,
        },
        token
      );
      setClips([]);
      setMusic(null);
      onCreated(job);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 402
          ? `${err.message}. Обновите тариф на странице «Тариф».`
          : err instanceof ApiError
            ? err.message
            : "Не удалось отправить на монтаж"
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="panel montage-form">
      <label className="field-block">
        <span className="field-label">Название (необязательно)</span>
        <input value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
      </label>

      <ChipSelect
        label="Формат"
        value={aspectRatio}
        options={[
          { value: "9:16", label: "9:16 · Reels, Shorts, TikTok" },
          { value: "16:9", label: "16:9 · YouTube" },
          { value: "1:1", label: "1:1 · лента" },
        ]}
        onChange={setAspectRatio}
      />

      <div className="field-block">
        <span className="field-label">Таймлайн — клипы идут в этом порядке</span>
        {clips.length === 0 ? (
          <p className="timeline-empty muted">Пока пусто — добавьте аватар-видео или загрузите файл ниже.</p>
        ) : (
          <ol className="timeline">
            {clips.map((clip, index) => (
              <li key={clip.key} className={`clip ${clip.type}`}>
                <span className="clip-icon" aria-hidden="true">
                  {clip.type === "image" ? <ImageIcon size={18} /> : <Film size={18} />}
                </span>
                <span className="clip-name">
                  <b>{String(index + 1).padStart(2, "0")} · {clip.type === "image" ? "Картинка" : "Видео"}</b>
                  <small title={clip.name}>{clip.name}</small>
                </span>
                {clip.type === "image" && (
                  <input
                    type="number"
                    min={0.5}
                    max={30}
                    step={0.5}
                    value={clip.duration_seconds}
                    aria-label="Длительность картинки, сек"
                    className="clip-seconds"
                    onChange={(e) =>
                      setClips((current) =>
                        current.map((c) => (c.key === clip.key ? { ...c, duration_seconds: Number(e.target.value) } : c))
                      )
                    }
                  />
                )}
                <span className="clip-actions">
                  <button type="button" className="ghost" onClick={() => move(index, -1)} disabled={index === 0} aria-label="Выше">
                    <ChevronUp size={16} />
                  </button>
                  <button type="button" className="ghost" onClick={() => move(index, 1)} disabled={index === clips.length - 1} aria-label="Ниже">
                    <ChevronDown size={16} />
                  </button>
                  <button type="button" className="ghost" onClick={() => setClips((c) => c.filter((x) => x.key !== clip.key))} aria-label="Убрать">
                    <X size={16} />
                  </button>
                </span>
              </li>
            ))}
          </ol>
        )}
        {music && (
          <div className="music">
            <Music size={16} aria-hidden="true" />
            <span>Музыка · {music.name}</span>
            <button type="button" className="ghost" onClick={() => setMusic(null)} aria-label="Убрать музыку">
              <X size={16} />
            </button>
          </div>
        )}
      </div>

      {avatarVideos.length > 0 && (
        <label className="field-block">
          <span className="field-label">Добавить аватар-видео</span>
          <select
            value=""
            onChange={(e) => {
              const job = avatarVideos.find((j) => j.id === e.target.value);
              if (job?.video_url) addClip({ type: "video", url: job.video_url, name: job.script.slice(0, 40), duration_seconds: 3 });
            }}
          >
            <option value="">выберите…</option>
            {avatarVideos.map((job) => (
              <option key={job.id} value={job.id}>
                {new Date(job.created_at).toLocaleString("ru-RU")} — {job.script.slice(0, 50)}
              </option>
            ))}
          </select>
        </label>
      )}
      <div className="form-row">
        <label className="drop">
          <Upload size={16} aria-hidden="true" /> Добавить клип
          <small>видео MP4/MOV или картинка PNG/JPEG/WebP</small>
          <input type="file" accept="video/mp4,video/quicktime,image/png,image/jpeg,image/webp" onChange={handleClipFile} disabled={uploading} />
        </label>
        <label className="drop">
          <Music size={16} aria-hidden="true" /> Фоновая музыка
          <small>MP3, WAV или M4A, необязательно</small>
          <input type="file" accept="audio/mpeg,audio/wav,audio/mp4,.m4a" onChange={handleMusicFile} disabled={uploading} />
        </label>
      </div>

      <label>
        <input type="checkbox" checked={subtitles} onChange={(e) => setSubtitles(e.target.checked)} /> Добавить субтитры
        (DaVinci распознает речь и вшьёт их в видео)
      </label>
      {subtitles && (
        <ChipSelect
          label="Язык речи"
          value={language}
          options={[
            { value: "auto", label: "Определить автоматически" },
            { value: "russian", label: "Русский" },
            { value: "english", label: "Английский" },
          ]}
          onChange={setLanguage}
        />
      )}

      {uploading && <p className="muted">Загружаем файл…</p>}
      {error && <p className="error">{error}</p>}
      <div className="list-row-actions">
        <button type="submit" disabled={submitting || uploading || clips.length === 0}>
          <Scissors size={16} aria-hidden="true" /> {submitting ? "Отправляем…" : "Смонтировать в DaVinci"}
        </button>
        <button
          type="button"
          className="secondary"
          disabled={submitting || uploading || clips.length === 0}
          onClick={() => create(false)}
        >
          Только монтажный лист
        </button>
      </div>
      <p className="muted hint">
        «Только монтажный лист» — архив с таймлайном и всеми материалами, чтобы смонтировать самому в своём
        DaVinci Resolve, даже бесплатном. Субтитры в него не входят — их делает DaVinci при рендере.
      </p>
    </form>
  );
}

function PackageControls({ job, onUpdated }: { job: MontageJob; onUpdated: (job: MontageJob) => void }) {
  const { token } = useAuth();
  const [error, setError] = useState<string | null>(null);

  async function request() {
    setError(null);
    try {
      onUpdated(await api.post<MontageJob>(`/montage/jobs/${job.id}/package`, {}, token));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось собрать архив");
    }
  }

  if (job.package_status === "building") {
    return <p className="muted">Собираем архив с монтажным листом и материалами…</p>;
  }
  if (job.package_status === "ready" && job.package_url) {
    const url = job.package_url;
    return (
      <>
        <button type="button" className="secondary" onClick={() => window.location.assign(url)}>
          Скачать монтажный лист (.zip)
        </button>
        <p className="muted">
          В DaVinci: File → Import → Timeline… → timeline.fcpxml из архива; когда DaVinci спросит, где клипы, —
          укажите распакованную папку. Подробно — в инструкции внутри архива.
        </p>
      </>
    );
  }
  return (
    <>
      {job.package_status === "failed" && <p className="error">{job.package_error ?? "Архив не собрался"}</p>}
      {error && <p className="error">{error}</p>}
      <button type="button" className="secondary" onClick={request}>
        {job.package_status === "failed" ? "Собрать архив заново" : "Скачать монтажный лист для DaVinci"}
      </button>
    </>
  );
}

function MontageCard({
  job,
  accounts,
  onUpdated,
}: {
  job: MontageJob;
  accounts: SocialAccount[];
  onUpdated: (job: MontageJob) => void;
}) {
  const [publishing, setPublishing] = useState(false);
  return (
    <div className="panel">
      <p>
        <strong>{job.title ?? `Монтаж из ${job.spec.clips.length} клипов`}</strong>{" "}
        <span className={`badge ${job.status}`}>{job.render ? STATUS_LABELS[job.status] : "монтажный лист"}</span>
      </p>
      {(job.status === "queued" || job.status === "processing") && (
        <>
          <p className="muted">{job.stage}</p>
          <div className="progress" role="progressbar" aria-valuenow={job.progress} aria-valuemin={0} aria-valuemax={100}>
            <span style={{ width: `${job.progress}%` }} />
          </div>
        </>
      )}
      {job.error_message && <p className="error">{job.error_message}</p>}
      {job.result_video_url && !publishing && (
        <>
          <video src={job.result_video_url} controls playsInline preload="metadata" style={{ width: "100%", maxHeight: 420 }} />
          <div className="list-row-actions">
            <button type="button" onClick={() => setPublishing(true)}>Опубликовать</button>
            <SaveToDriveButton url={job.result_video_url} title={job.title} />
          </div>
        </>
      )}
      {publishing && job.result_video_url && (
        <ReviewAndPublish
          videoUrl={job.result_video_url}
          contentKind="post"
          initialCaption={job.title ?? ""}
          accounts={accounts}
          targetAccountIds={[]}
        />
      )}
      {!publishing && <PackageControls job={job} onUpdated={onUpdated} />}
    </div>
  );
}

function MontageStudio() {
  const { token } = useAuth();
  const [jobs, setJobs] = useState<MontageJob[] | null>(null);
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);

  const reload = useCallback(() => {
    api
      .get<Page<MontageJob>>("/montage/jobs?limit=30", token)
      .then((page) => setJobs(page.items))
      .catch(() => setJobs((current) => current ?? []));
  }, [token]);

  useEffect(reload, [reload]);
  useEffect(() => {
    api.get<SocialAccount[]>("/social-accounts", token).then(setAccounts).catch(() => setAccounts([]));
  }, [token]);

  const active =
    jobs?.some(
      (job) => job.status === "queued" || job.status === "processing" || job.package_status === "building"
    ) ?? false;
  const replace = useCallback(
    (updated: MontageJob) => setJobs((current) => current?.map((job) => (job.id === updated.id ? updated : job)) ?? null),
    []
  );
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(reload, POLL_MS);
    return () => clearInterval(timer);
  }, [active, reload]);

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Монтаж</h1>
          <p className="muted">Соберите историю из клипов, музыки и субтитров — DaVinci смонтирует и отрендерит.</p>
        </div>
      </div>
      <DaVinciStatus />
      <Suspense fallback={null}>
        <MontageBuilder onCreated={(job) => setJobs((current) => [job, ...(current ?? [])])} />
      </Suspense>

      <div className="section-head section">
        <h2>Ваши монтажи</h2>
      </div>
      {jobs === null && <div className="skeleton" aria-busy="true" />}
      {jobs?.length === 0 && <p className="muted">Пока ничего не смонтировано.</p>}
      <div className="variant-grid">
        {jobs?.map((job) => <MontageCard key={job.id} job={job} accounts={accounts} onUpdated={replace} />)}
      </div>
    </>
  );
}

export default function MontagePage() {
  return (
    <RequireAuth>
      <MontageStudio />
    </RequireAuth>
  );
}
