"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

import { RequireAuth } from "../../../components/RequireAuth";

function YouTubeCallback() {
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
      setError(oauthError);
      return;
    }
    const code = searchParams.get("code");
    const state = searchParams.get("state");
    if (!code || !state) {
      setError("В ответе Google нет кода авторизации или state");
      return;
    }

    api
      .post("/social-accounts/youtube/connect", { code, state }, token)
      .then(() => router.push("/social-accounts"))
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Не удалось подключить YouTube")
      );
  }, [token, searchParams, router]);

  if (error) {
    return (
      <>
        <h1>Не удалось подключить YouTube</h1>
        <p className="error">{error}</p>
      </>
    );
  }

  return <p className="muted">Подключаем YouTube…</p>;
}

export default function YouTubeCallbackPage() {
  return (
    <RequireAuth>
      <YouTubeCallback />
    </RequireAuth>
  );
}
