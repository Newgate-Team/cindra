"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type {
  DriveExport,
  HeyGenAccount,
  HeyGenAvatar,
  Integration,
  Page,
  TokenPage,
} from "@/lib/types";

import { RequireAuth } from "../components/RequireAuth";

function formatBalance(account: HeyGenAccount): string | null {
  if (account.remaining_balance === null) return null;
  const currency = (account.currency ?? "usd").toUpperCase();
  return `${account.remaining_balance.toFixed(2)} ${currency}`;
}

function HeyGenKeyForm({ onConnected, submitLabel }: { onConnected: () => void; submitLabel: string }) {
  const { token } = useAuth();
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await api.put("/integrations/heygen", { api_key: apiKey }, token);
      setApiKey("");
      onConnected();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось подключить HeyGen");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <label>
        API-ключ HeyGen
        <input
          type="password"
          required
          minLength={8}
          autoComplete="off"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder="вставьте ключ"
        />
      </label>
      <p className="muted">
        Где взять: HeyGen → Settings → API → API token. Ключ проверяется у HeyGen при подключении
        и хранится у нас в зашифрованном виде — в интерфейсе он больше не показывается.
      </p>
      {error && <p className="error">{error}</p>}
      <button type="submit" disabled={submitting}>
        {submitting ? "Проверяем ключ…" : submitLabel}
      </button>
    </form>
  );
}

function HeyGenConnected({ integration, onChanged }: { integration: Integration; onChanged: () => void }) {
  const { token } = useAuth();
  const [account, setAccount] = useState<HeyGenAccount | null>(null);
  const [avatars, setAvatars] = useState<HeyGenAvatar[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [replacing, setReplacing] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);

  useEffect(() => {
    api
      .get<HeyGenAccount>("/integrations/heygen/account", token)
      .then(setAccount)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Не удалось связаться с HeyGen"));
    // A failed fetch leaves avatars null (nothing shown) rather than [],
    // which would wrongly claim the account has no avatars -- the
    // account call above already surfaces why HeyGen didn't answer.
    api
      .get<TokenPage<HeyGenAvatar>>("/integrations/heygen/avatars?ownership=private", token)
      .then((page) => setAvatars(page.items))
      .catch(() => setAvatars(null));
  }, [token, integration.account_label]);

  async function handleDisconnect() {
    setDisconnecting(true);
    try {
      await api.delete("/integrations/heygen", token);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось отключить HeyGen");
      setDisconnecting(false);
    }
  }

  const balance = account ? formatBalance(account) : null;

  return (
    <>
      <p>
        Подключён: <strong>{integration.account_label ?? "аккаунт HeyGen"}</strong>
      </p>
      <p>
        <Link href="/video/avatar">Создать аватар-видео →</Link>
      </p>
      {!integration.is_own && (
        <p className="muted">
          Этот аккаунт подключил участник вашей команды — генерации оплачиваются с его кошелька
          HeyGen. Можно подключить свой ключ, тогда ваши генерации пойдут с вашего аккаунта.
        </p>
      )}
      {balance && <p className="muted">Баланс HeyGen API: {balance}</p>}
      {account?.remaining_balance === 0 && (
        <p className="error">
          На балансе HeyGen API нет средств — генерация видео не пройдёт, пока вы не пополните
          кошелёк в HeyGen.
        </p>
      )}
      {error && <p className="error">{error}</p>}

      {avatars && avatars.length > 0 && (
        <>
          <p className="muted">Ваши аватары в HeyGen:</p>
          <div className="avatar-grid">
            {avatars.slice(0, 12).map((avatar) => (
              <div key={avatar.id} className="avatar-tile">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={avatar.preview_image_url ?? ""} alt="" />
                <span title={avatar.name}>{avatar.name}</span>
              </div>
            ))}
          </div>
        </>
      )}
      {avatars && avatars.length === 0 && (
        <p className="muted">
          Своих аватаров в HeyGen пока нет — для генерации будут доступны публичные аватары HeyGen.
        </p>
      )}

      {replacing ? (
        <HeyGenKeyForm
          submitLabel={integration.is_own ? "Заменить ключ" : "Подключить свой ключ"}
          onConnected={() => {
            setReplacing(false);
            onChanged();
          }}
        />
      ) : (
        <div className="list-row-actions">
          <button type="button" className="secondary" onClick={() => setReplacing(true)}>
            {integration.is_own ? "Заменить ключ" : "Подключить свой ключ"}
          </button>
          <button type="button" className="secondary" onClick={handleDisconnect} disabled={disconnecting}>
            {disconnecting ? "Отключаем…" : "Отключить"}
          </button>
        </div>
      )}
    </>
  );
}

const EXPORT_STATUS: Record<DriveExport["status"], string> = {
  queued: "в очереди",
  processing: "сохраняется…",
  completed: "сохранён",
  failed: "ошибка",
};

function ConnectGoogleDriveButton({ label }: { label: string }) {
  const { token } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  async function handleClick() {
    setError(null);
    setStarting(true);
    try {
      const { authorization_url } = await api.post<{ authorization_url: string }>(
        "/integrations/google-drive/start",
        undefined,
        token
      );
      window.location.href = authorization_url;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось начать подключение");
      setStarting(false);
    }
  }

  return (
    <>
      <button type="button" onClick={handleClick} disabled={starting}>
        {starting ? "Открываем Google…" : label}
      </button>
      {error && <p className="error">{error}</p>}
    </>
  );
}

function DriveExports() {
  const { token } = useAuth();
  const [exports, setExports] = useState<DriveExport[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = () =>
      api
        .get<Page<DriveExport>>("/integrations/google-drive/exports?limit=10", token)
        .then((page) => {
          if (cancelled) return;
          setExports(page.items);
          // Keep watching while something is still on its way to Drive.
          if (page.items.some((item) => item.status === "queued" || item.status === "processing")) {
            timer = setTimeout(load, 4000);
          }
        })
        .catch(() => !cancelled && setExports(null));
    load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [token]);

  if (!exports || exports.length === 0) return null;
  return (
    <>
      <p className="muted">Последние сохранения:</p>
      <ul className="drive-exports">
        {exports.map((item) => (
          <li key={item.id}>
            <span className="drive-export-name" title={item.file_name}>
              {item.file_name}
            </span>{" "}
            {item.status === "completed" && item.web_view_link ? (
              <a href={item.web_view_link} target="_blank" rel="noreferrer">
                открыть
              </a>
            ) : (
              <span className={item.status === "failed" ? "error" : "muted"}>
                {EXPORT_STATUS[item.status]}
              </span>
            )}
            {item.status === "failed" && item.error_message && (
              <span className="muted"> — {item.error_message}</span>
            )}
          </li>
        ))}
      </ul>
    </>
  );
}

function GoogleDriveConnected({
  integration,
  onChanged,
}: {
  integration: Integration;
  onChanged: () => void;
}) {
  const { token } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function setAutoSave(autoSave: boolean) {
    setError(null);
    setBusy(true);
    try {
      await api.patch("/integrations/google-drive", { auto_save: autoSave }, token);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить настройку");
    } finally {
      setBusy(false);
    }
  }

  async function handleDisconnect() {
    setError(null);
    setBusy(true);
    try {
      await api.delete("/integrations/google-drive", token);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось отключить Google Drive");
      setBusy(false);
    }
  }

  return (
    <>
      <p>
        Подключён: <strong>{integration.account_label ?? "аккаунт Google"}</strong>
        {integration.folder_url && (
          <>
            {" · "}
            <a href={integration.folder_url} target="_blank" rel="noreferrer">
              Открыть папку «Cindra»
            </a>
          </>
        )}
      </p>
      {!integration.is_own && (
        <p className="muted">
          Этот Google Drive подключил участник вашей команды — ваш контент сохраняется в его папку
          «Cindra». Можно подключить свой Google Drive, тогда файлы пойдут к вам.
        </p>
      )}
      <label>
        <input
          type="checkbox"
          checked={integration.auto_save ?? true}
          disabled={busy || !integration.is_own}
          onChange={(e) => setAutoSave(e.target.checked)}
        />
        Сохранять готовые изображения, видео и монтажи автоматически
      </label>
      {error && <p className="error">{error}</p>}
      <DriveExports />
      <div className="list-row-actions">
        <ConnectGoogleDriveButton
          label={integration.is_own ? "Подключить другой аккаунт" : "Подключить свой Google Drive"}
        />
        {integration.is_own && (
          <button type="button" className="secondary" onClick={handleDisconnect} disabled={busy}>
            Отключить
          </button>
        )}
      </div>
    </>
  );
}

function IntegrationsManager() {
  const { token } = useAuth();
  const [integrations, setIntegrations] = useState<Integration[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    api
      .get<Integration[]>("/integrations", token)
      .then(setIntegrations)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Не удалось загрузить"));
  }, [token]);

  useEffect(reload, [reload]);

  const heygen = integrations?.find((item) => item.provider === "heygen") ?? null;
  const drive = integrations?.find((item) => item.provider === "google_drive") ?? null;

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Инструменты</h1>
          <p className="muted">
            Подключите свои аккаунты сервисов — Cindra генерирует в них и складывает туда готовый
            контент от вашего имени, а оплата идёт с вашего аккаунта у сервиса.
          </p>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {integrations === null && !error && <p className="muted">Загрузка…</p>}

      {integrations !== null && (
        <>
          <h2>HeyGen — AI-аватары</h2>
          <div className="card">
            {heygen ? (
              <HeyGenConnected integration={heygen} onChanged={reload} />
            ) : (
              <>
                <p className="muted">
                  Видео с вашим аватаром и голосом по сценарию. Генерация оплачивается с вашего
                  кошелька HeyGen API.
                </p>
                <HeyGenKeyForm submitLabel="Подключить HeyGen" onConnected={reload} />
              </>
            )}
          </div>

          <h2>Google Drive — хранилище</h2>
          <div className="card">
            {drive ? (
              <GoogleDriveConnected integration={drive} onChanged={reload} />
            ) : (
              <>
                <p className="muted">
                  Готовые изображения, видео и монтажи сами складываются в папку «Cindra» на вашем
                  Google Диске. Cindra получает доступ только к файлам, которые создаёт сама, —
                  остальное содержимое Диска ей не видно.
                </p>
                <ConnectGoogleDriveButton label="Подключить Google Drive" />
              </>
            )}
          </div>
        </>
      )}
    </>
  );
}

export default function IntegrationsPage() {
  return (
    <RequireAuth>
      <IntegrationsManager />
    </RequireAuth>
  );
}
