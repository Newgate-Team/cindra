"use client";

import { useState, type FormEvent } from "react";

import { RequireAuth } from "../components/RequireAuth";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { UserRole } from "@/lib/types";

function SettingsForm() {
  const { user, token, refreshUser } = useAuth();
  // user is never null here -- RequireAuth (the only caller) already
  // waits for it, but hooks below still need something to seed from.
  const [role, setRole] = useState<UserRole>(user?.role ?? "solo");
  const [shareToFeed, setShareToFeed] = useState(user?.share_generations_to_feed ?? true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!user) return null;

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSaved(false);
    setSaving(true);
    try {
      await api.patch(
        "/auth/me",
        { role, share_generations_to_feed: shareToFeed },
        token
      );
      await refreshUser();
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Ваша студия — ваши правила</h1>
          <p className="muted">Профиль, безопасность и приватность.</p>
        </div>
      </div>
      <div className="settings-stack">
      <form onSubmit={handleSubmit} className="panel settings-form">
        <h2>Профиль</h2>
        <div className="list-line">
          <div className="list-line-text">
            <b>{user.email}</b>
            <small>{user.email_verified ? "Email подтверждён" : "Email ещё не подтверждён"}</small>
          </div>
          <span className={`tag${user.email_verified ? " green" : ""}`}>{user.email_verified ? "Подтверждён" : "Ждёт подтверждения"}</span>
        </div>
        {/* Only agency accounts have anything to decide about "Тип
            аккаунта" -- for solo there's nothing here worth surfacing
            (they registered as solo and stay solo; switching TO agency
            isn't a self-service flow this page offers). */}
        {user.role === "agency" && (
          <label>
            Тип аккаунта
            <select value={role} onChange={(e) => setRole(e.target.value as UserRole)}>
              <option value="agency">Агентство</option>
              <option value="solo">Соло-предприниматель / крео</option>
            </select>
          </label>
        )}
        <h2>Приватность</h2>
        <label className="toggle-row">
          <span>
            <b>Показывать мои работы в общей Ленте</b>
            <small className="muted">
              Лента — общая витрина сгенерированного контента без указания автора. Если выключить, готовые
              изображения и видео увидите только вы. Аватар-видео остаются приватными всегда.
            </small>
          </span>
          <input
            type="checkbox"
            checked={shareToFeed}
            onChange={(e) => setShareToFeed(e.target.checked)}
          />
        </label>
        {error && <p className="error">{error}</p>}
        {saved && <p className="muted">Сохранено.</p>}
        <button type="submit" disabled={saving}>
          {saving ? "Сохраняем…" : "Сохранить"}
        </button>
      </form>
      {/* A Google-only account (has_password: false) has no password
          to change -- /auth/login already points it at "Войти через
          Google" instead, this form would just 400 every time. */}
      {user.has_password && <ChangePasswordForm />}
      </div>
    </>
  );
}

function ChangePasswordForm() {
  const { token } = useAuth();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSaved(false);
    if (newPassword !== confirmPassword) {
      setError("Новые пароли не совпадают");
      return;
    }
    setSaving(true);
    try {
      await api.post(
        "/auth/change-password",
        { current_password: currentPassword, new_password: newPassword },
        token
      );
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сменить пароль");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="panel settings-form">
      <h2>Сменить пароль</h2>
      <label>
        Текущий пароль
        <input
          type="password"
          required
          value={currentPassword}
          onChange={(e) => setCurrentPassword(e.target.value)}
        />
      </label>
      <label>
        Новый пароль (минимум 8 символов)
        <input
          type="password"
          required
          minLength={8}
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
        />
      </label>
      <label>
        Повторите новый пароль
        <input
          type="password"
          required
          minLength={8}
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
        />
      </label>
      {error && <p className="error">{error}</p>}
      {saved && <p className="muted">Пароль изменён.</p>}
      <button type="submit" disabled={saving}>
        {saving ? "Сохраняем…" : "Сменить пароль"}
      </button>
    </form>
  );
}

export default function SettingsPage() {
  return (
    <RequireAuth>
      <SettingsForm />
    </RequireAuth>
  );
}
