"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { Team } from "@/lib/types";

function AcceptInviteContent() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const { user, token: authToken, loading: authLoading } = useAuth();
  const [status, setStatus] = useState<"pending" | "done" | "error">("pending");
  const [error, setError] = useState<string | null>(null);
  const [team, setTeam] = useState<Team | null>(null);

  useEffect(() => {
    if (!token || authLoading || !user) return;
    api
      .post<Team>("/team/invites/accept", { token }, authToken)
      .then((joined) => {
        setTeam(joined);
        setStatus("done");
      })
      .catch((err) => {
        setStatus("error");
        setError(err instanceof ApiError ? err.message : "Не удалось принять приглашение");
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, authLoading, user]);

  if (!token) {
    return (
      <>
        <h1>Приглашение в команду</h1>
        <p className="error">Ссылка неполная — в ней нет токена.</p>
      </>
    );
  }

  if (authLoading) {
    return (
      <>
        <h1>Приглашение в команду</h1>
        <p className="muted">Загрузка…</p>
      </>
    );
  }

  if (!user) {
    return (
      <>
        <h1>Приглашение в команду</h1>
        <p className="muted">
          Войдите (или зарегистрируйтесь) под тем email, на который пришло приглашение, чтобы
          принять его.
        </p>
        <p className="muted">
          <Link href={`/login?next=${encodeURIComponent(`/team/accept?token=${token}`)}`}>
            Войти
          </Link>
        </p>
      </>
    );
  }

  if (status === "pending") {
    return (
      <>
        <h1>Приглашение в команду</h1>
        <p className="muted">Принимаем приглашение…</p>
      </>
    );
  }

  if (status === "error") {
    return (
      <>
        <h1>Приглашение в команду</h1>
        <p className="error">{error}</p>
      </>
    );
  }

  return (
    <>
      <h1>Добро пожаловать в команду{team ? ` «${team.name}»` : ""}!</h1>
      <p className="muted">
        <Link href="/team">Перейти к команде</Link>
      </p>
    </>
  );
}

export default function AcceptTeamInvitePage() {
  return (
    <Suspense fallback={<p className="muted">Загрузка…</p>}>
      <AcceptInviteContent />
    </Suspense>
  );
}
