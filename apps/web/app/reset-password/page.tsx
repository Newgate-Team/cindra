"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";

import { ApiError, api } from "@/lib/api";

function ResetPasswordForm() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (newPassword !== confirmPassword) {
      setError("Пароли не совпадают");
      return;
    }
    setSubmitting(true);
    try {
      await api.post("/auth/password-reset/confirm", { token, new_password: newPassword });
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сменить пароль");
    } finally {
      setSubmitting(false);
    }
  }

  if (!token) {
    return (
      <>
        <h1>Сброс пароля</h1>
        <p className="error">
          Ссылка неполная — в ней нет токена. Запросите новую ссылку для сброса пароля.
        </p>
        <p className="muted">
          <Link href="/forgot-password">Запросить ссылку заново</Link>
        </p>
      </>
    );
  }

  if (done) {
    return (
      <>
        <h1>Пароль изменён</h1>
        <p className="muted">Теперь можно войти с новым паролем.</p>
        <p className="muted">
          <Link href="/login">Войти</Link>
        </p>
      </>
    );
  }

  return (
    <>
      <h1>Новый пароль</h1>
      <form onSubmit={handleSubmit}>
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
        <button type="submit" disabled={submitting}>
          {submitting ? "Сохраняем…" : "Сменить пароль"}
        </button>
      </form>
      <p className="muted">
        Ссылка недействительна или устарела?{" "}
        <Link href="/forgot-password">Запросите новую</Link>
      </p>
    </>
  );
}

// useSearchParams() needs a Suspense boundary above it in the App
// Router, or the build fails prerendering this page (it wants to bail
// out to client-side rendering for the ?token= part specifically).
export default function ResetPasswordPage() {
  return (
    <Suspense fallback={<p className="muted">Загрузка…</p>}>
      <ResetPasswordForm />
    </Suspense>
  );
}
