"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

import { RequireAuth } from "../../../components/RequireAuth";

function TwitchCallback() {
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
          ? "Вы не разрешили Cindra читать чат Twitch"
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
        "/integrations/twitch/connect",
        { code, state },
        token
      )
      .then(() => router.push("/integrations"))
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Не удалось подключить Twitch")
      );
  }, [token, searchParams, router]);

  if (error) {
    return (
      <>
        <h1>Не удалось подключить Twitch</h1>
        <p className="error">{error}</p>
        <p>
          <Link href="/integrations">← К инструментам</Link>
        </p>
      </>
    );
  }

  return <p className="muted">Подключаем Twitch…</p>;
}

export default function TwitchCallbackPage() {
  return (
    <RequireAuth>
      <TwitchCallback />
    </RequireAuth>
  );
}
