"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

import { RequireAuth } from "../../../components/RequireAuth";

function GoogleDriveCallback() {
  const { token } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current || !token) return;
    started.current = true;

    const oauthError = searchParams.get("error_description") ?? searchParams.get("error");
    if (oauthError) {
      setError(
        oauthError === "access_denied"
          ? "Вы не разрешили Cindra сохранять файлы в Google Drive"
          : oauthError
      );
      return;
    }
    const code = searchParams.get("code");
    const state = searchParams.get("state");
    if (!code || !state) {
      setError("В ответе Google нет кода авторизации или state");
      return;
    }

    api
      .post(
        "/integrations/google-drive/connect",
        // Saved files are named in this zone, not UTC.
        { code, state, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone },
        token
      )
      .then(() => router.push("/integrations"))
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Не удалось подключить Google Drive")
      );
  }, [token, searchParams, router]);

  if (error) {
    return (
      <>
        <h1>Не удалось подключить Google Drive</h1>
        <p className="error">{error}</p>
        <p>
          <Link href="/integrations">← К инструментам</Link>
        </p>
      </>
    );
  }

  return <p className="muted">Подключаем Google Drive…</p>;
}

export default function GoogleDriveCallbackPage() {
  return (
    <RequireAuth>
      <GoogleDriveCallback />
    </RequireAuth>
  );
}
