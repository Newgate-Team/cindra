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
          <h1>Настройки</h1>
          <p className="muted">{user.email}</p>
        </div>
      </div>
      <form onSubmit={handleSubmit} className="card">
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
        <label>
          <input
            type="checkbox"
            checked={shareToFeed}
            onChange={(e) => setShareToFeed(e.target.checked)}
          />
          {" "}Показывать мои готовые изображения и видео в общей Ленте
        </label>
        <p className="muted">
          Лента — общая витрина сгенерированного контента всех пользователей, без указания
          автора. Если выключить, готовые изображения и видео останутся видны только вам.
        </p>
        {error && <p className="error">{error}</p>}
        {saved && <p className="muted">Сохранено.</p>}
        <button type="submit" disabled={saving}>
          {saving ? "Сохраняем…" : "Сохранить"}
        </button>
      </form>
    </>
  );
}

export default function SettingsPage() {
  return (
    <RequireAuth>
      <SettingsForm />
    </RequireAuth>
  );
}
