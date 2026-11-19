"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

import { RequireAuth } from "../../../components/RequireAuth";

function RedditCallback() {
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
      setError("В ответе Reddit нет кода авторизации или state");
      return;
    }

    api
      .post("/social-accounts/reddit/connect", { code, state }, token)
      .then(() => router.push("/social-accounts"))
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Не удалось подключить Reddit")
      );
  }, [token, searchParams, router]);

  if (error) {
    return (
      <>
        <h1>Не удалось подключить Reddit</h1>
        <p className="error">{error}</p>
      </>
    );
  }

  return <p className="muted">Подключаем Reddit…</p>;
}

export default function RedditCallbackPage() {
  return (
    <RequireAuth>
      <RedditCallback />
    </RequireAuth>
  );
}
