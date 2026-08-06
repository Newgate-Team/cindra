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

function SocialAccountsManager() {
  const { token } = useAuth();
  const [accounts, setAccounts] = useState<SocialAccount[] | null>(null);
  const [chatId, setChatId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);

  function reload() {
    api.get<SocialAccount[]>("/social-accounts", token).then(setAccounts);
  }

  useEffect(reload, [token]);

  async function handleConnectTelegram(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setConnecting(true);
    try {
      await api.post("/social-accounts/telegram/connect", { chat_id: chatId }, token);
      setChatId("");
      reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось подключить канал");
    } finally {
      setConnecting(false);
    }
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
      <h1>Подключённые соцсети</h1>

      {accounts === null && <p className="muted">Загрузка…</p>}
      {accounts?.length === 0 && <p className="muted">Пока ничего не подключено.</p>}
      {accounts?.map((account) => (
        <div key={account.id} className="card" style={{ display: "flex", alignItems: "center" }}>
          <div style={{ flex: 1 }}>
            <strong>{PLATFORM_LABELS[account.platform] ?? account.platform}</strong> —{" "}
            {account.display_name ?? account.external_account_id}
          </div>
          <button className="secondary" onClick={() => handleDisconnect(account.id)}>
            Отключить
          </button>
        </div>
      ))}

      <h2>Подключить Telegram-канал</h2>
      <p className="muted">
        Бот @CindraPublish_bot должен быть добавлен администратором канала. Введите @username
        канала или его числовой chat_id.
      </p>
      <form onSubmit={handleConnectTelegram}>
        <label>
          Канал
          <input
            required
            value={chatId}
            onChange={(e) => setChatId(e.target.value)}
            placeholder="@mychannel"
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={connecting}>
          {connecting ? "Подключаем…" : "Подключить"}
        </button>
      </form>

      <h2>Подключить Instagram</h2>
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
