"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

function VerifyEmailContent() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const { user, refreshUser } = useAuth();
  const [status, setStatus] = useState<"pending" | "done" | "error">("pending");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    api
      .post("/auth/verify-email/confirm", { token })
      .then(() => {
        setStatus("done");
        // Refreshes the shared user object if this device also happens
        // to be logged in -- harmless no-op otherwise (refreshUser is a
        // no-op without a token, see auth-context.tsx).
        refreshUser();
      })
      .catch((err) => {
        setStatus("error");
        setError(err instanceof ApiError ? err.message : "Не удалось подтвердить email");
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  if (!token) {
    return (
      <>
        <h1>Подтверждение email</h1>
        <p className="error">Ссылка неполная — в ней нет токена.</p>
      </>
    );
  }

  if (status === "pending") {
    return (
      <>
        <h1>Подтверждение email</h1>
        <p className="muted">Проверяем ссылку…</p>
      </>
    );
  }

  if (status === "error") {
    return (
      <>
        <h1>Подтверждение email</h1>
        <p className="error">{error}</p>
        {user && (
          <p className="muted">
            <Link href="/settings">Запросить новую ссылку в настройках</Link>
          </p>
        )}
      </>
    );
  }

  return (
    <>
      <h1>Email подтверждён</h1>
      <p className="muted">Спасибо — адрес подтверждён.</p>
      <p className="muted">
        <Link href={user ? "/generate" : "/login"}>{user ? "Вернуться в приложение" : "Войти"}</Link>
      </p>
    </>
  );
}

// Same reason as /reset-password -- useSearchParams() needs a Suspense
// boundary above it or the build fails prerendering this page.
export default function VerifyEmailPage() {
  return (
    <Suspense fallback={<p className="muted">Загрузка…</p>}>
      <VerifyEmailContent />
    </Suspense>
  );
}
