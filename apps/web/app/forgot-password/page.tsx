"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { ApiError, api } from "@/lib/api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await api.post("/auth/password-reset/request", { email });
      // Backend always returns the same generic response whether or
      // not the email is registered -- no need to distinguish here.
      setSent(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось отправить запрос");
    } finally {
      setSubmitting(false);
    }
  }

  if (sent) {
    return (
      <>
        <h1>Проверьте почту</h1>
        <p className="muted">
          Если такой email зарегистрирован, мы отправили на него ссылку для сброса пароля.
        </p>
        <p className="muted">
          <Link href="/login">Назад ко входу</Link>
        </p>
      </>
    );
  }

  return (
    <>
      <h1>Забыли пароль?</h1>
      <p className="muted">Укажите email — вышлем ссылку для сброса пароля.</p>
      <form onSubmit={handleSubmit}>
        <label>
          Email
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={submitting}>
          {submitting ? "Отправляем…" : "Отправить ссылку"}
        </button>
      </form>
      <p className="muted">
        <Link href="/login">Назад ко входу</Link>
      </p>
    </>
  );
}
