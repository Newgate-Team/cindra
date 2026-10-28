"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type {
  LiveAvatarAvatar,
  LiveStream,
  LiveStreamStatus,
  StreamEvent,
  StreamProvider,
  StreamVoice,
} from "@/lib/types";

import { RequireAuth } from "../components/RequireAuth";

const STATUS_LABELS: Record<LiveStreamStatus, string> = {
  draft: "не запущен",
  queued: "ждёт сервер эфира",
  live: "в эфире",
  ended: "завершён",
  failed: "ошибка",
};

const STATUS_BADGE: Record<LiveStreamStatus, string> = {
  draft: "draft",
  queued: "flagged",
  live: "active",
  ended: "draft",
  failed: "failed",
};

const EVENT_LABELS: Record<StreamEvent["kind"], string> = {
  reply: "ответ",
  idle: "тема",
  notice: "приветствие",
  blocked: "пропущено",
  system: "эфир",
  error: "ошибка",
};

const PERSONA_EXAMPLE =
  "Ты Синди — весёлая и дружелюбная ведущая канала про маркетинг в соцсетях. " +
  "Говоришь просто, с юмором, любишь практичные советы. Канал ведёт агентство Newgate.";

function formatUptime(seconds?: number): string {
  if (!seconds) return "0 мин";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h ? `${h} ч ${m} мин` : `${m} мин`;
}

interface FormState {
  title: string;
  channel: string;
  stream_key: string;
  avatar_provider: string;
  avatar_id: string;
  voice_id: string;
  persona: string;
  idle_topics: string;
  blocked_terms: string;
  min_reply_interval_seconds: number;
  max_hours: number;
  post_replies_to_chat: boolean;
}

function emptyForm(provider: string): FormState {
  return {
    title: "",
    channel: "",
    stream_key: "",
    avatar_provider: provider,
    avatar_id: "",
    voice_id: "Kore",
    persona: "",
    idle_topics: "",
    blocked_terms: "",
    min_reply_interval_seconds: 15,
    max_hours: 24,
    post_replies_to_chat: false,
  };
}

function formFrom(stream: LiveStream): FormState {
  return {
    title: stream.title,
    channel: stream.channel,
    stream_key: "",
    avatar_provider: stream.avatar_provider,
    avatar_id: stream.avatar.avatar_id ?? "",
    voice_id: stream.avatar.voice_id ?? "Kore",
    persona: stream.persona,
    idle_topics: stream.idle_topics,
    blocked_terms: stream.blocked_terms.join(", "),
    min_reply_interval_seconds: stream.min_reply_interval_seconds,
    max_hours: stream.max_hours,
    post_replies_to_chat: stream.post_replies_to_chat,
  };
}

function AvatarPicker({
  provider,
  value,
  disabled,
  onChange,
}: {
  provider: string;
  value: string;
  disabled: boolean;
  onChange: (avatar: LiveAvatarAvatar) => void;
}) {
  const { token } = useAuth();
  const [ownership, setOwnership] = useState<"private" | "public">("private");
  const [avatars, setAvatars] = useState<LiveAvatarAvatar[]>([]);
  const [nextPage, setNextPage] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(
    (page: number, replace: boolean) => {
      setLoading(true);
      setError(null);
      api
        .get<{ items: LiveAvatarAvatar[]; next_page: number | null }>(
          `/integrations/${provider}/avatars?ownership=${ownership}&page=${page}`,
          token
        )
        .then((result) => {
          setAvatars((current) => (replace ? result.items : [...current, ...result.items]));
          setNextPage(result.next_page);
        })
        .catch((err) => setError(err instanceof ApiError ? err.message : "Не удалось загрузить аватары"))
        .finally(() => setLoading(false));
    },
    [ownership, provider, token]
  );

  useEffect(() => load(1, true), [load]);

  return (
    <div>
      <p>Аватар</p>
      <div className="list-row-actions">
        <button
          type="button"
          className={ownership === "private" ? "" : "secondary"}
          onClick={() => setOwnership("private")}
        >
          Мои
        </button>
        <button
          type="button"
          className={ownership === "public" ? "" : "secondary"}
          onClick={() => setOwnership("public")}
        >
          Публичные
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {!error && !loading && avatars.length === 0 && (
        <p className="muted">
          {ownership === "private" ? "Своих аватаров пока нет — выберите публичный." : "Список пуст."}
        </p>
      )}
      <div className="avatar-grid">
        {avatars.map((avatar) => (
          <button
            key={avatar.id}
            type="button"
            className={`avatar-tile${avatar.id === value ? " selected" : ""}`}
            aria-pressed={avatar.id === value}
            disabled={disabled || !avatar.ready}
            title={avatar.ready ? avatar.name : `${avatar.name} — ещё обучается`}
            onClick={() => onChange(avatar)}
          >
            {avatar.preview_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={avatar.preview_url} alt="" />
            ) : (
              // Simli's own faces come without a picture.
              <div className="avatar-placeholder">{avatar.name.slice(0, 1)}</div>
            )}
            <span>{avatar.ready ? avatar.name : `${avatar.name} · обучается`}</span>
          </button>
        ))}
      </div>
      {nextPage && (
        <button type="button" className="secondary" disabled={loading} onClick={() => load(nextPage, false)}>
          {loading ? "Загружаем…" : "Показать ещё"}
        </button>
      )}
    </div>
  );
}

function StreamForm({
  stream,
  providers,
  voices,
  twitchConnected,
  onSaved,
}: {
  stream: LiveStream | null;
  providers: StreamProvider[];
  voices: StreamVoice[];
  twitchConnected: boolean;
  onSaved: (stream: LiveStream) => void;
}) {
  const { token } = useAuth();
  const [form, setForm] = useState<FormState>(() =>
    stream ? formFrom(stream) : emptyForm((providers.find((p) => p.connected) ?? providers[0])?.id ?? "")
  );
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const onAir = stream?.status === "queued" || stream?.status === "live";
  const provider = providers.find((p) => p.id === form.avatar_provider);

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSaving(true);
    const common = {
      title: form.title,
      persona: form.persona,
      idle_topics: form.idle_topics,
      blocked_terms: form.blocked_terms
        .split(/[,\n]/)
        .map((term) => term.trim())
        .filter(Boolean),
      min_reply_interval_seconds: form.min_reply_interval_seconds,
      max_hours: form.max_hours,
    };
    // Channel, key and avatar can't change on air -- only send them
    // when the stream is stopped (the API refuses them otherwise).
    const offAir = {
      channel: form.channel,
      avatar: { avatar_id: form.avatar_id, voice_id: form.voice_id || null },
      post_replies_to_chat: form.post_replies_to_chat,
      ...(form.stream_key ? { stream_key: form.stream_key } : {}),
    };
    try {
      const saved = stream
        ? await api.patch<LiveStream>(
            `/streams/${stream.id}`,
            onAir ? common : { ...common, ...offAir },
            token
          )
        : await api.post<LiveStream>(
            "/streams",
            { ...common, ...offAir, avatar_provider: form.avatar_provider },
            token
          );
      setForm((current) => ({ ...current, stream_key: "" }));
      onSaved(saved);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <label>
        Название эфира
        <input required maxLength={200} value={form.title} onChange={(e) => set("title", e.target.value)} />
      </label>
      <label>
        Канал Twitch (логин)
        <input
          required
          disabled={onAir}
          value={form.channel}
          onChange={(e) => set("channel", e.target.value)}
          placeholder="например, cindra_live"
        />
      </label>
      <label>
        Ключ трансляции Twitch
        <input
          type="password"
          autoComplete="off"
          disabled={onAir}
          value={form.stream_key}
          onChange={(e) => set("stream_key", e.target.value)}
          placeholder={stream?.stream_key_set ? "сохранён — введите новый, чтобы заменить" : "live_..."}
        />
      </label>
      <p className="muted">
        Twitch → Панель автора → Настройки → Трансляция → «Основной ключ трансляции». Ключ хранится
        зашифрованным и больше не показывается.
      </p>

      {!stream && (
        <label>
          Сервис аватара
          <select
            value={form.avatar_provider}
            onChange={(e) => {
              // An avatar id belongs to one vendor: pick again for the new one.
              const provider = e.target.value;
              setForm((current) => ({ ...current, avatar_provider: provider, avatar_id: "" }));
            }}
          >
            {providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
                {p.connected ? "" : " — не подключён"}
              </option>
            ))}
          </select>
        </label>
      )}
      {provider && !provider.connected && (
        <p className="error">
          {provider.label} не подключён — <Link href="/integrations">подключите свой ключ</Link>, иначе эфир
          не запустится.
        </p>
      )}
      {provider?.connected && (
        <AvatarPicker
          provider={form.avatar_provider}
          value={form.avatar_id}
          disabled={onAir}
          onChange={(avatar) => set("avatar_id", avatar.id)}
        />
      )}
      <label>
        Голос
        <select disabled={onAir} value={form.voice_id} onChange={(e) => set("voice_id", e.target.value)}>
          {voices.map((voice) => (
            <option key={voice.id} value={voice.id}>
              {voice.label}
            </option>
          ))}
        </select>
      </label>

      <label>
        Кто ведёт эфир и о чём канал
        <textarea
          required
          minLength={10}
          maxLength={4000}
          rows={4}
          value={form.persona}
          onChange={(e) => set("persona", e.target.value)}
          placeholder={PERSONA_EXAMPLE}
        />
      </label>
      <label>
        О чём говорить, когда чат молчит (тема на строку)
        <textarea rows={3} maxLength={4000} value={form.idle_topics} onChange={(e) => set("idle_topics", e.target.value)} />
      </label>
      <label>
        Запрещённые слова (через запятую)
        <input value={form.blocked_terms} onChange={(e) => set("blocked_terms", e.target.value)} placeholder="названия конкурентов, …" />
      </label>
      <div className="form-row">
        <label>
          Пауза между ответами, сек
          <input
            type="number"
            min={5}
            max={300}
            value={form.min_reply_interval_seconds}
            onChange={(e) => set("min_reply_interval_seconds", Number(e.target.value))}
          />
        </label>
        <label>
          Длительность эфира, ч
          <input
            type="number"
            min={1}
            max={72}
            value={form.max_hours}
            onChange={(e) => set("max_hours", Number(e.target.value))}
          />
        </label>
      </div>
      <label>
        <input
          type="checkbox"
          disabled={onAir || !twitchConnected}
          checked={form.post_replies_to_chat && twitchConnected}
          onChange={(e) => set("post_replies_to_chat", e.target.checked)}
        />
        Дублировать ответы аватара сообщениями в чат Twitch
      </label>
      <p className="muted">
        {twitchConnected
          ? "Чат читается через подключённый аккаунт Twitch (официальный API)."
          : "Чат читается анонимно. Подключите Twitch на странице «Инструменты» — чтение станет надёжнее, и аватар сможет писать в чат."}
      </p>
      {onAir && (
        <p className="muted">
          В эфире можно менять персону, темы, запрещённые слова и паузу — применится к следующему ответу.
        </p>
      )}
      {error && <p className="error">{error}</p>}
      <button type="submit" disabled={saving || !form.avatar_id}>
        {saving ? "Сохраняем…" : stream ? "Сохранить" : "Создать эфир"}
      </button>
    </form>
  );
}

function StreamLog({ stream }: { stream: LiveStream }) {
  const { token } = useAuth();
  const [events, setEvents] = useState<StreamEvent[]>([]);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api
        .get<StreamEvent[]>(`/streams/${stream.id}/events?limit=50`, token)
        .then((items) => !cancelled && setEvents(items))
        .catch(() => undefined);
    load();
    if (stream.status !== "live" && stream.status !== "queued") return () => void (cancelled = true);
    const timer = setInterval(load, 3000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [stream.id, stream.status, token]);

  if (events.length === 0) return <p className="muted">В журнале эфира пока пусто.</p>;
  return (
    <ul className="stream-log">
      {events.map((event) => (
        <li key={event.id} className={`stream-log-${event.kind}`}>
          <span className="muted">
            {new Date(event.created_at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}{" "}
            · {EVENT_LABELS[event.kind]}
          </span>
          {event.author && event.message && (
            <span className="stream-log-question">
              <strong>{event.author}:</strong> {event.message}
            </span>
          )}
          <span>{event.text}</span>
        </li>
      ))}
    </ul>
  );
}

function StreamCard({
  stream,
  providers,
  voices,
  twitchConnected,
  onChanged,
}: {
  stream: LiveStream;
  providers: StreamProvider[];
  voices: StreamVoice[];
  twitchConnected: boolean;
  onChanged: (stream: LiveStream | null) => void;
}) {
  const { token } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const onAir = stream.status === "queued" || stream.status === "live";

  async function action(path: string) {
    setError(null);
    setBusy(true);
    try {
      onChanged(await api.post<LiveStream>(`/streams/${stream.id}/${path}`, undefined, token));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не получилось");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.delete(`/streams/${stream.id}`, token);
      onChanged(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось удалить");
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <p>
        <strong>{stream.title}</strong>{" "}
        <span className={`badge ${STATUS_BADGE[stream.status]}`}>{STATUS_LABELS[stream.status]}</span>
      </p>
      <p className="muted">
        twitch.tv/{stream.channel}
        {stream.status === "live" && (
          <>
            {" · "}в эфире {formatUptime(stream.stats.uptime_seconds)} · ответов {stream.stats.replies ?? 0} ·
            сообщений в чате {stream.stats.chat_messages ?? 0}
          </>
        )}
        {stream.stop_requested && " · останавливается…"}
      </p>
      {stream.error_message && <p className="error">{stream.error_message}</p>}
      {error && <p className="error">{error}</p>}
      <div className="list-row-actions">
        {onAir ? (
          <button type="button" onClick={() => action("stop")} disabled={busy || stream.stop_requested}>
            Остановить эфир
          </button>
        ) : (
          <button type="button" onClick={() => action("start")} disabled={busy}>
            Запустить эфир
          </button>
        )}
        <button type="button" className="secondary" onClick={() => setEditing((v) => !v)}>
          {editing ? "Скрыть настройки" : "Настройки"}
        </button>
        {!onAir && (
          <button type="button" className="secondary" onClick={remove} disabled={busy}>
            Удалить
          </button>
        )}
      </div>
      {editing && (
        <StreamForm
          stream={stream}
          providers={providers}
          voices={voices}
          twitchConnected={twitchConnected}
          onSaved={(saved) => {
            setEditing(false);
            onChanged(saved);
          }}
        />
      )}
      <h3>Журнал эфира</h3>
      <StreamLog stream={stream} />
    </div>
  );
}

function StreamsManager() {
  const { token } = useAuth();
  const [streams, setStreams] = useState<LiveStream[] | null>(null);
  const [providers, setProviders] = useState<StreamProvider[]>([]);
  const [voices, setVoices] = useState<StreamVoice[]>([]);
  const [twitchConnected, setTwitchConnected] = useState(false);
  const [agentOnline, setAgentOnline] = useState<boolean | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    api
      .get<LiveStream[]>("/streams", token)
      .then(setStreams)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Не удалось загрузить"));
    api
      .get<{ online: boolean }>("/streams/agents/status", token)
      .then((s) => setAgentOnline(s.online))
      .catch(() => setAgentOnline(null));
  }, [token]);

  useEffect(() => {
    reload();
    api.get<StreamProvider[]>("/streams/providers", token).then(setProviders).catch(() => undefined);
    api.get<StreamVoice[]>("/streams/voices", token).then(setVoices).catch(() => undefined);
    api
      .get<{ provider: string }[]>("/integrations", token)
      .then((items) => setTwitchConnected(items.some((item) => item.provider === "twitch")))
      .catch(() => undefined);
  }, [reload, token]);

  // Status, uptime and counters move while a stream is on air.
  const anyOnAir = streams?.some((s) => s.status === "queued" || s.status === "live");
  useEffect(() => {
    if (!anyOnAir) return;
    const timer = setInterval(reload, 5000);
    return () => clearInterval(timer);
  }, [anyOnAir, reload]);

  function replace(id: string, next: LiveStream | null) {
    setStreams((current) =>
      next ? (current ?? []).map((s) => (s.id === id ? next : s)) : (current ?? []).filter((s) => s.id !== id)
    );
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Эфир с AI-аватаром</h1>
          <p className="muted">
            Аватар ведёт трансляцию на Twitch круглосуточно: читает чат, отвечает голосом, приветствует
            рейды и подписчиков, а когда чат молчит — сам говорит на ваши темы. Минуты аватара
            оплачиваются с вашего аккаунта у сервиса аватара.
          </p>
        </div>
      </div>
      {agentOnline === false && (
        <p className="muted">
          Сервер эфира сейчас не подключён — запущенный эфир начнётся, как только он появится.
        </p>
      )}
      {error && <p className="error">{error}</p>}
      {streams === null && !error && <p className="muted">Загрузка…</p>}

      {streams !== null && providers.length === 0 && (
        <p className="muted">Сервисы аватаров для эфира пока не настроены на сервере.</p>
      )}

      {streams !== null && providers.length > 0 && (
        <>
          {creating ? (
            <div className="card">
              <h2>Новый эфир</h2>
              <StreamForm
                stream={null}
                providers={providers}
                voices={voices}
                twitchConnected={twitchConnected}
                onSaved={(stream) => {
                  setCreating(false);
                  setStreams((current) => [stream, ...(current ?? [])]);
                }}
              />
            </div>
          ) : (
            <button type="button" onClick={() => setCreating(true)}>
              Новый эфир
            </button>
          )}
          {streams.map((stream) => (
            <StreamCard
              key={stream.id}
              stream={stream}
              providers={providers}
              voices={voices}
              twitchConnected={twitchConnected}
              onChanged={(next) => replace(stream.id, next)}
            />
          ))}
        </>
      )}
    </>
  );
}

export default function StreamsPage() {
  return (
    <RequireAuth>
      <StreamsManager />
    </RequireAuth>
  );
}
