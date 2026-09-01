"use client";

import { useParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from "react";

import { RequireAuth } from "../../components/RequireAuth";
import {
  TikTokPublishFields,
  useTikTokPublishOptions,
} from "../../components/TikTokPublishFields";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { allowedContentTypesFor } from "@/lib/publish-matrix";
import type { Post, SocialAccount, VideoProject, VideoStyle } from "@/lib/types";

const VIDEO_POLL_INTERVAL_MS = 5000;

function minDatetimeLocal(): string {
  return new Date().toISOString().slice(0, 16);
}

function downloadFile(filename: string, content: string) {
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function PublishBlock({ project }: { project: VideoProject }) {
  const { token } = useAuth();
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [caption, setCaption] = useState(project.topic);
  const [scheduledFor, setScheduledFor] = useState("");
  const [posts, setPosts] = useState<Post[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [publishing, setPublishing] = useState(false);

  useEffect(() => {
    if (!token) return;
    api
      .get<SocialAccount[]>("/social-accounts", token)
      .then((all) =>
        setAccounts(
          all.filter((account) => allowedContentTypesFor([account.platform]).includes("video"))
        )
      )
      .catch(() => setAccounts([]));
  }, [token]);

  const selectedTikTokAccounts = useMemo(
    () =>
      accounts.filter(
        (account) => selectedIds.includes(account.id) && account.platform === "tiktok"
      ),
    [accounts, selectedIds]
  );
  // veo_auto clips are AI-generated (disclosure locked on); a video
  // shot and edited by the user from a brief is not, but they can
  // still mark it AI-made themselves.
  const aiGenerated = project.style === "veo_auto";
  const tiktok = useTikTokPublishOptions(selectedTikTokAccounts, token, { aiGenerated });

  function toggleAccount(accountId: string, checked: boolean) {
    setSelectedIds((previous) =>
      checked ? [...previous, accountId] : previous.filter((id) => id !== accountId)
    );
  }

  async function handlePublish(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setPublishing(true);
    try {
      const created = await api.post<Post[]>(
        "/posts",
        {
          social_account_ids: selectedIds,
          text: caption,
          video_url: project.video_url,
          content_kind: "post",
          scheduled_for: scheduledFor ? new Date(scheduledFor).toISOString() : null,
          platform_options: tiktok.platformOptions,
        },
        token
      );
      setPosts(created);
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        setError("Лимит публикаций по тарифу исчерпан.");
      } else {
        setError(err instanceof ApiError ? err.message : "Не удалось опубликовать");
      }
    } finally {
      setPublishing(false);
    }
  }

  if (accounts.length === 0) {
    return (
      <p className="muted">
        Чтобы опубликовать ролик из Cindra, подключите аккаунт с поддержкой видео на
        странице «Каналы».
      </p>
    );
  }

  return (
    <form onSubmit={handlePublish}>
      <fieldset>
        <legend>Куда опубликовать</legend>
        {accounts.map((account) => (
          <label key={account.id}>
            <input
              type="checkbox"
              checked={selectedIds.includes(account.id)}
              onChange={(e) => toggleAccount(account.id, e.target.checked)}
            />
            {account.platform} — {account.display_name ?? account.external_account_id}
          </label>
        ))}
      </fieldset>
      <label>
        Подпись к публикации
        <textarea rows={4} value={caption} onChange={(e) => setCaption(e.target.value)} />
      </label>
      <TikTokPublishFields
        accounts={selectedTikTokAccounts}
        creators={tiktok.creators}
        options={tiktok.options}
        onChange={tiktok.updateOption}
        loading={tiktok.loading}
        error={tiktok.error}
        aigcLocked={aiGenerated}
      />
      <label>
        Запланировать на (необязательно — иначе публикуем сразу)
        <input
          type="datetime-local"
          min={minDatetimeLocal()}
          value={scheduledFor}
          onChange={(e) => setScheduledFor(e.target.value)}
        />
      </label>
      {error && <p className="error">{error}</p>}
      <button
        type="submit"
        disabled={publishing || selectedIds.length === 0 || tiktok.loading || !tiktok.ready}
      >
        {publishing ? "Публикуем…" : scheduledFor ? "Запланировать" : "Опубликовать сейчас"}
      </button>
      {posts && (
        <div>
          {posts.map((post) => (
            <div key={post.id} className="card list-row">
              <div className="list-row-body">
                <strong>{post.platform}</strong>
                <p className="muted list-row-meta">
                  <span>{post.account_label}</span>
                </p>
                {post.status === "failed" && post.error_message && (
                  <p className="error">{post.error_message}</p>
                )}
              </div>
              <div className="list-row-side">
                <span className={`badge ${post.status}`}>{post.status}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </form>
  );
}

function VideoProjectWizard() {
  const { token } = useAuth();
  const params = useParams<{ id: string }>();
  const [project, setProject] = useState<VideoProject | null>(null);
  const [styles, setStyles] = useState<VideoStyle[]>([]);
  const [scriptDraft, setScriptDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!token) return;
    api
      .get<VideoProject>(`/video-projects/${params.id}`, token)
      .then((loaded) => {
        setProject(loaded);
        setScriptDraft(loaded.script ?? "");
      })
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Не удалось загрузить проект")
      );
    api.get<VideoStyle[]>("/video-projects/styles", token).then(setStyles).catch(() => {});
  }, [token, params.id]);

  // While a Veo generation or any illustration job is in flight, poll
  // the project until every linked job reaches a terminal state.
  useEffect(() => {
    if (!token || !project) return;
    const inFlight =
      project.video_status === "queued" ||
      project.video_status === "processing" ||
      (project.illustrations ?? []).some(
        (i) => i.status === "queued" || i.status === "processing"
      );
    if (!inFlight) return;
    pollRef.current = setInterval(() => {
      api
        .get<VideoProject>(`/video-projects/${project.id}`, token)
        .then(setProject)
        .catch(() => {});
    }, VIDEO_POLL_INTERVAL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [token, project]);

  async function run(label: string, action: () => Promise<VideoProject>) {
    setError(null);
    setBusy(label);
    try {
      const updated = await action();
      setProject(updated);
      setScriptDraft(updated.script ?? "");
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        setError("Лимит генераций по тарифу исчерпан.");
      } else {
        setError(err instanceof ApiError ? err.message : "Что-то пошло не так");
      }
    } finally {
      setBusy(null);
    }
  }

  function generateScript() {
    run("script", () =>
      api.post<VideoProject>(`/video-projects/${params.id}/script`, undefined, token)
    );
  }

  function saveScript() {
    run("save-script", () =>
      api.patch<VideoProject>(`/video-projects/${params.id}`, { script: scriptDraft }, token)
    );
  }

  function chooseStyle(styleId: string) {
    run(`style-${styleId}`, () =>
      api.patch<VideoProject>(`/video-projects/${params.id}`, { style: styleId }, token)
    );
  }

  function generateBrief() {
    run("brief", () =>
      api.post<VideoProject>(`/video-projects/${params.id}/brief`, undefined, token)
    );
  }

  function generateIllustrations() {
    run("illustrations", () =>
      api.post<VideoProject>(`/video-projects/${params.id}/illustrations`, undefined, token)
    );
  }

  function generateVeo() {
    run("veo", () =>
      api.post<VideoProject>(`/video-projects/${params.id}/video-generation`, undefined, token)
    );
  }

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    run("upload", () =>
      api.upload<VideoProject>(`/video-projects/${params.id}/video`, file, token)
    );
  }

  if (error && !project) return <p className="error">{error}</p>;
  if (!project) return <p className="muted">Загружаем…</p>;

  const selectedStyle = styles.find((style) => style.id === project.style);
  const veoInFlight =
    project.video_status === "queued" || project.video_status === "processing";

  return (
    <>
      <h1>Видео-проект</h1>
      <p className="muted">{project.topic}</p>

      <section className="card">
        <h2>1. Сценарий</h2>
        {project.script === null ? (
          <button type="button" onClick={generateScript} disabled={busy !== null}>
            {busy === "script" ? "Генерируем…" : "Сгенерировать сценарий"}
          </button>
        ) : (
          <>
            <label>
              Сценарий (можно отредактировать)
              <textarea
                rows={10}
                value={scriptDraft}
                onChange={(e) => setScriptDraft(e.target.value)}
              />
            </label>
            <div className="list-row-actions">
              <button
                type="button"
                onClick={saveScript}
                disabled={busy !== null || scriptDraft === (project.script ?? "")}
              >
                {busy === "save-script" ? "Сохраняем…" : "Сохранить"}
              </button>
              <button type="button" onClick={generateScript} disabled={busy !== null}>
                {busy === "script" ? "Генерируем…" : "Перегенерировать"}
              </button>
            </div>
          </>
        )}
      </section>

      {project.script !== null && (
        <section className="card">
          <h2>2. Стиль</h2>
          <div>
            {styles.map((style) => (
              <label key={style.id} className="card list-row">
                <div className="list-row-body">
                  <input
                    type="radio"
                    name="style"
                    checked={project.style === style.id}
                    onChange={() => chooseStyle(style.id)}
                    disabled={busy !== null}
                  />
                  <strong> {style.title}</strong>
                  <p className="muted list-row-meta">
                    <span>{style.description}</span>
                  </p>
                </div>
              </label>
            ))}
          </div>
        </section>
      )}

      {project.script !== null && selectedStyle && selectedStyle.produces === "brief" && (
        <section className="card">
          <h2>3. Производственный бриф</h2>
          {project.brief_files === null ? (
            <button type="button" onClick={generateBrief} disabled={busy !== null}>
              {busy === "brief" ? "Собираем бриф…" : "Сгенерировать бриф"}
            </button>
          ) : (
            <>
              {project.brief_files.map((file) => (
                <div key={file.filename} className="card">
                  <div className="list-row">
                    <div className="list-row-body">
                      <strong>{file.title}</strong>
                    </div>
                    <div className="list-row-side">
                      <button
                        type="button"
                        onClick={() => downloadFile(file.filename, file.content)}
                      >
                        Скачать {file.filename}
                      </button>
                    </div>
                  </div>
                  <pre className="brief-content">{file.content}</pre>
                </div>
              ))}
              <button type="button" onClick={generateBrief} disabled={busy !== null}>
                {busy === "brief" ? "Собираем бриф…" : "Перегенерировать бриф"}
              </button>
              {selectedStyle.generates_illustrations && (
                <div className="illustrations-block">
                  <h3>Иллюстрации</h3>
                  {project.illustrations === null ? (
                    <>
                      <p className="muted">
                        Cindra может сгенерировать иллюстрации из продакшн-плана сама —
                        каждая считается как одна генерация изображения по тарифу.
                      </p>
                      <button
                        type="button"
                        onClick={generateIllustrations}
                        disabled={busy !== null}
                      >
                        {busy === "illustrations"
                          ? "Запускаем…"
                          : "Сгенерировать иллюстрации"}
                      </button>
                    </>
                  ) : (
                    <>
                      <div className="illustrations-grid">
                        {project.illustrations.map((illustration, index) => (
                          <figure key={index} className="card illustration-card">
                            {illustration.image_url ? (
                               
                              <a href={illustration.image_url} target="_blank" rel="noreferrer">
                                {/* eslint-disable-next-line @next/next/no-img-element */}
                                <img src={illustration.image_url} alt={illustration.prompt} />
                              </a>
                            ) : illustration.status === "failed" ? (
                              <p className="error">
                                {illustration.error_message ?? "Не удалось сгенерировать"}
                              </p>
                            ) : (
                              <p className="muted">Генерируем…</p>
                            )}
                            <figcaption className="muted">{illustration.prompt}</figcaption>
                          </figure>
                        ))}
                      </div>
                      <button
                        type="button"
                        onClick={generateIllustrations}
                        disabled={busy !== null}
                      >
                        {busy === "illustrations"
                          ? "Запускаем…"
                          : "Перегенерировать иллюстрации"}
                      </button>
                    </>
                  )}
                </div>
              )}
            </>
          )}
        </section>
      )}

      {project.script !== null && selectedStyle && selectedStyle.produces === "clip" && (
        <section className="card">
          <h2>3. Генерация ролика</h2>
          {project.video_url === null && !veoInFlight && (
            <button type="button" onClick={generateVeo} disabled={busy !== null}>
              {busy === "veo" ? "Запускаем…" : "Сгенерировать ролик (Veo)"}
            </button>
          )}
          {veoInFlight && <p className="muted">Генерируем ролик — это занимает пару минут…</p>}
          {project.video_status === "failed" && (
            <p className="error">{project.video_error ?? "Генерация не удалась"}</p>
          )}
        </section>
      )}

      {project.script !== null && (
        <section className="card">
          <h2>{selectedStyle ? "4" : "3"}. Готовое видео</h2>
          {project.video_url ? (
            <video src={project.video_url} controls style={{ maxWidth: "100%", borderRadius: 8 }} />
          ) : (
            <p className="muted">
              Смонтируйте ролик по брифу и загрузите готовый файл — или сгенерируйте его
              стилем «Полное авто».
            </p>
          )}
          <label>
            {project.video_url ? "Заменить файл" : "Загрузить готовое видео (mp4, mov, webm)"}
            <input
              type="file"
              accept="video/mp4,video/quicktime,video/webm"
              onChange={handleUpload}
              disabled={busy !== null}
            />
          </label>
          {busy === "upload" && <p className="muted">Загружаем файл…</p>}
          {project.video_url && <PublishBlock project={project} />}
        </section>
      )}

      {error && <p className="error">{error}</p>}
    </>
  );
}

export default function VideoProjectPage() {
  return (
    <RequireAuth>
      <VideoProjectWizard />
    </RequireAuth>
  );
}
