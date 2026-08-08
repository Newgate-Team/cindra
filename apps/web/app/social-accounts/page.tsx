"use client";

import { useEffect, useState, type FormEvent } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { SocialAccount } from "@/lib/types";

import { RequireAuth } from "../components/RequireAuth";

// Real Meta OAuth redirect once a Meta App exists -- see gate ticket
// CIN-52. Until then this stays unset and the button below explains
// why, instead of sending the user into a broken flow.
const META_APP_ID = process.env.NEXT_PUBLIC_META_APP_ID;
const META_REDIRECT_URI = process.env.NEXT_PUBLIC_META_REDIRECT_URI;

const PLATFORM_LABELS: Record<string, string> = {
  telegram: "Telegram",
  instagram: "Instagram",
  facebook: "Facebook-страница",
};

const PLATFORM_ICONS: Record<string, string> = {
  telegram: "/telegram-icon.png",
  instagram: "/instagram-icon.png",
  facebook: "/facebook-icon.png",
};

interface TelegramVerification {
  code: string;
  chatTitle: string | null;
  verificationToken: string;
}

function SocialAccountsManager() {
  const { token } = useAuth();
  const [accounts, setAccounts] = useState<SocialAccount[] | null>(null);
  const [chatId, setChatId] = useState("");
  const [verification, setVerification] = useState<TelegramVerification | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [connectError, setConnectError] = useState<string | null>(null);
  const [startingVerification, setStartingVerification] = useState(false);
  const [connecting, setConnecting] = useState(false);

  function reload() {
    api.get<SocialAccount[]>("/social-accounts", token).then(setAccounts);
  }

  useEffect(reload, [token]);

  // CIN-128: connecting a channel is two steps now -- start-verification
  // finds the chat and issues a one-time code, then the user has to
  // actually place that code in the channel's description (an admin-only
  // Telegram permission) before /connect will accept it. This is what
  // proves the person clicking "Подключить" here really controls the
  // channel, instead of just knowing its @username.
  async function handleStartVerification(event: FormEvent) {
    event.preventDefault();
    setStartError(null);
    setStartingVerification(true);
    try {
      const result = await api.post<{
        code: string;
        verification_token: string;
        chat_title: string | null;
      }>("/social-accounts/telegram/start-verification", { chat_id: chatId }, token);
      setVerification({
        code: result.code,
        verificationToken: result.verification_token,
        chatTitle: result.chat_title,
      });
    } catch (err) {
      setStartError(err instanceof ApiError ? err.message : "Не удалось найти канал");
    } finally {
      setStartingVerification(false);
    }
  }

  async function handleConfirmConnect() {
    if (!verification) return;
    setConnectError(null);
    setConnecting(true);
    try {
      await api.post(
        "/social-accounts/telegram/connect",
        { verification_token: verification.verificationToken },
        token
      );
      setChatId("");
      setVerification(null);
      reload();
    } catch (err) {
      setConnectError(err instanceof ApiError ? err.message : "Не удалось подключить канал");
    } finally {
      setConnecting(false);
    }
  }

  function handleCancelVerification() {
    setVerification(null);
    setConnectError(null);
  }

  function handleConnectInstagram() {
    if (!META_APP_ID) return;
    const url = new URL("https://www.facebook.com/v21.0/dialog/oauth");
    url.searchParams.set("client_id", META_APP_ID);
    url.searchParams.set("redirect_uri", META_REDIRECT_URI ?? "");
    url.searchParams.set(
      "scope",
      // pages_manage_posts/pages_manage_engagement/pages_read_user_content
      // added for CIN-65 (publishing to the Facebook Page itself, not
      // just the Instagram account linked to it) -- connected in the
      // same OAuth round-trip, no separate consent screen.
      "instagram_basic,instagram_content_publish,pages_show_list,pages_read_engagement," +
        "business_management,pages_manage_posts,pages_manage_engagement,pages_read_user_content"
    );
    window.location.href = url.toString();
  }

  async function handleDisconnect(id: string) {
    await api.delete(`/social-accounts/${id}`, token);
    reload();
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Каналы</h1>
          {accounts !== null && (
            <p className="muted">
              {accounts.length > 0 ? `Подключено: ${accounts.length}` : "Пока ничего не подключено"}
            </p>
          )}
        </div>
      </div>

      {accounts === null && <p className="muted">Загрузка…</p>}
      {accounts && accounts.length > 0 && (
        <div className="tile-grid">
          {accounts.map((account) => {
            const label = PLATFORM_LABELS[account.platform] ?? account.platform;
            return (
              <div key={account.id} className="card">
                <div className="tile-header">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={PLATFORM_ICONS[account.platform]} alt="" className="platform-icon" />
                  <div className="tile-header-body">
                    <strong>{label}</strong>
                    <span className="muted">{account.display_name ?? account.external_account_id}</span>
                  </div>
                  <span className="badge active">Подключён</span>
                </div>
                <button className="secondary" onClick={() => handleDisconnect(account.id)}>
                  Отключить
                </button>
              </div>
            );
          })}
        </div>
      )}

      <h2>Подключить Telegram-канал</h2>
      <div className="card">
        {!verification ? (
          <>
            <p className="muted">
              Бот @CindraPublish_bot должен быть добавлен администратором канала. Введите
              @username канала или его числовой chat_id.
            </p>
            <form onSubmit={handleStartVerification}>
              <label>
                Канал
                <input
                  required
                  value={chatId}
                  onChange={(e) => setChatId(e.target.value)}
                  placeholder="@mychannel"
                />
              </label>
              {startError && <p className="error">{startError}</p>}
              <button type="submit" disabled={startingVerification}>
                {startingVerification ? "Ищем канал…" : "Далее"}
              </button>
            </form>
          </>
        ) : (
          <>
            <p>
              Канал найден: <strong>{verification.chatTitle ?? chatId}</strong>
            </p>
            <p className="muted">
              Чтобы подтвердить, что вы администратор канала (а не просто знаете его адрес),
              вставьте этот код в описание канала в Telegram (Изменить → Описание) и сохраните,
              затем нажмите «Подключить». После подключения код можно убрать.
            </p>
            <div className="verification-code-row">
              <code className="verification-code">{verification.code}</code>
              <button
                type="button"
                className="secondary"
                onClick={() => navigator.clipboard.writeText(verification.code)}
              >
                Скопировать
              </button>
            </div>
            {connectError && <p className="error">{connectError}</p>}
            <div className="list-row-actions">
              <button type="button" disabled={connecting} onClick={handleConfirmConnect}>
                {connecting ? "Проверяем…" : "Подключить"}
              </button>
              <button type="button" className="secondary" onClick={handleCancelVerification}>
                Отмена
              </button>
            </div>
          </>
        )}
      </div>

      <h2>Подключить Instagram</h2>
      <div className="card">
        {META_APP_ID ? (
          <>
            <p className="muted">Перед подключением убедитесь, что:</p>
            <ul className="muted">
              <li>Instagram-аккаунт переведён в профессиональный режим (Business или Creator);</li>
              <li>он привязан к Facebook-странице, которой вы управляете как администратор.</li>
            </ul>
            <p className="muted">
              <a
                href="https://developers.facebook.com/docs/instagram-platform"
                target="_blank"
                rel="noreferrer"
              >
                Подробнее в документации Meta
              </a>
            </p>
            <button onClick={handleConnectInstagram}>Войти через Meta</button>
            <p className="muted">
              Заодно подключится и сама Facebook-страница — она появится в списке выше отдельной
              записью, публиковать в неё можно так же, как в Telegram и Instagram.
            </p>
          </>
        ) : (
          <p className="muted">
            Подключение Instagram пока недоступно — не настроено Meta App (см. задачу CIN-52).
          </p>
        )}
      </div>
    </>
  );
}

export default function SocialAccountsPage() {
  return (
    <RequireAuth>
      <SocialAccountsManager />
    </RequireAuth>
  );
}
