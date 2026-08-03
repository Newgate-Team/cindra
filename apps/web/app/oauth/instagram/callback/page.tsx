"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

import { RequireAuth } from "../../../components/RequireAuth";

function InstagramCallback() {
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
    if (!code) {
      setError("В ответе от Meta нет кода авторизации");
      return;
    }

    api
      .post("/social-accounts/instagram/connect", { code }, token)
      .then(() => router.push("/social-accounts"))
      .catch((err) => setError(err instanceof ApiError ? err.message : "Не удалось подключить Instagram"));
  }, [token, searchParams, router]);

  if (error) {
    return (
      <>
        <h1>Не удалось подключить Instagram</h1>
        <p className="error">{error}</p>
      </>
    );
  }

  return <p className="muted">Подключаем Instagram…</p>;
}

export default function InstagramCallbackPage() {
  return (
    <RequireAuth>
      <InstagramCallback />
    </RequireAuth>
  );
}
