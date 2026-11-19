"use client";

import { useState } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Informational only (see User.email_verified) -- nothing in the
// product is gated on this, so the banner is just a reminder, not a
// blocker. Dismissing it is local-only (no persisted preference): it
// comes back on a full page reload, same tradeoff as most such
// reminders elsewhere in the app.
export function EmailVerificationBanner() {
  const { user, token } = useAuth();
  const [dismissed, setDismissed] = useState(false);
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!user || user.email_verified || dismissed) return null;

  async function handleResend() {
    setSending(true);
    setError(null);
    try {
      await api.post("/auth/verify-email/request", undefined, token);
      setSent(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось отправить письмо");
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="verification-banner">
      <span>
        {sent
          ? "Письмо отправлено — проверьте почту."
          : `Подтвердите email — мы отправили ссылку на ${user.email}.`}
      </span>
      {error && <span className="error">{error}</span>}
      {!sent && (
        <button className="secondary" onClick={handleResend} disabled={sending}>
          {sending ? "Отправляем…" : "Отправить повторно"}
        </button>
      )}
      <button className="icon-button" onClick={() => setDismissed(true)} aria-label="Скрыть">
        ×
      </button>
    </div>
  );
}
