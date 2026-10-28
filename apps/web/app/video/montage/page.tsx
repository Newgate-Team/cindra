"use client";

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
    <p className="muted">DaVinci-сервер на связи — монтаж начнётся сразу.</p>
  ) : (
    <p className="muted">
      DaVinci-сервер сейчас не на связи — монтаж встанет в очередь и начнётся, когда он подключится.
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
    <form onSubmit={handleSubmit} className="card">
      <label>
        Название (необязательно)
        <input value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
      </label>

      <p>Клипы — в том порядке, в каком пойдут в ролике</p>
      {clips.length === 0 && <p className="muted">Пока пусто — добавьте аватар-видео или загрузите файл.</p>}
      {clips.map((clip, index) => (
        <div key={clip.key} className="list-row-actions" style={{ alignItems: "center", marginBottom: 6 }}>
          <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {index + 1}. {clip.type === "image" ? "Картинка" : "Видео"}: {clip.name}
          </span>
          {clip.type === "image" && (
            <input
              type="number"
              min={0.5}
              max={30}
              step={0.5}
              value={clip.duration_seconds}
              aria-label="Длительность картинки, сек"
              style={{ width: 70 }}
              onChange={(e) =>
                setClips((current) =>
                  current.map((c) => (c.key === clip.key ? { ...c, duration_seconds: Number(e.target.value) } : c))
                )
              }
            />
          )}
          <button type="button" className="secondary" onClick={() => move(index, -1)} disabled={index === 0} aria-label="Выше">↑</button>
          <button type="button" className="secondary" onClick={() => move(index, 1)} disabled={index === clips.length - 1} aria-label="Ниже">↓</button>
          <button type="button" className="secondary" onClick={() => setClips((c) => c.filter((x) => x.key !== clip.key))} aria-label="Убрать">✕</button>
        </div>
      ))}

      {avatarVideos.length > 0 && (
        <label>
          Добавить аватар-видео
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
      <label>
        Загрузить клип (видео MP4/MOV или картинка PNG/JPEG/WebP)
        <input type="file" accept="video/mp4,video/quicktime,image/png,image/jpeg,image/webp" onChange={handleClipFile} disabled={uploading} />
      </label>

      <label>
        Фоновая музыка (MP3/WAV/M4A, необязательно)
        <input type="file" accept="audio/mpeg,audio/wav,audio/mp4,.m4a" onChange={handleMusicFile} disabled={uploading} />
      </label>
      {music && (
        <p className="muted">
          Музыка: {music.name}{" "}
          <button type="button" className="secondary" onClick={() => setMusic(null)}>убрать</button>
        </p>
      )}

      <label>
        Формат
        <select value={aspectRatio} onChange={(e) => setAspectRatio(e.target.value)}>
          <option value="9:16">9:16 — Reels, Shorts, TikTok</option>
          <option value="16:9">16:9 — YouTube</option>
          <option value="1:1">1:1 — лента</option>
        </select>
      </label>
      <label>
        <input type="checkbox" checked={subtitles} onChange={(e) => setSubtitles(e.target.checked)} /> Субтитры (DaVinci
        распознает речь и вшьёт их в видео)
      </label>
      {subtitles && (
        <label>
          Язык речи
          <select value={language} onChange={(e) => setLanguage(e.target.value)}>
            <option value="auto">определить автоматически</option>
            <option value="russian">русский</option>
            <option value="english">английский</option>
          </select>
        </label>
      )}

      {uploading && <p className="muted">Загружаем файл…</p>}
      {error && <p className="error">{error}</p>}
      <div className="list-row-actions">
        <button type="submit" disabled={submitting || uploading || clips.length === 0}>
          {submitting ? "Отправляем…" : "Смонтировать в DaVinci"}
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
      <p className="muted">
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
    <div className="card">
      <p>
        <strong>{job.title ?? `Монтаж из ${job.spec.clips.length} клипов`}</strong>{" "}
        <span className={`badge ${job.status}`}>{job.render ? STATUS_LABELS[job.status] : "монтажный лист"}</span>
      </p>
      {(job.status === "queued" || job.status === "processing") && (
        <>
          <p className="muted">{job.stage}</p>
          <div style={{ height: 6, background: "var(--muted)", borderRadius: 3 }}>
            <div style={{ width: `${job.progress}%`, height: 6, background: "var(--ember)", borderRadius: 3 }} />
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
          <p className="muted">
            Соберите ролик из аватар-видео, своих клипов и музыки — DaVinci смонтирует его, добавит субтитры и
            отрендерит.
          </p>
        </div>
      </div>
      <DaVinciStatus />
      <Suspense fallback={null}>
        <MontageBuilder onCreated={(job) => setJobs((current) => [job, ...(current ?? [])])} />
      </Suspense>

      <h2>Ваши монтажи</h2>
      {jobs === null && <p className="muted">Загрузка…</p>}
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
