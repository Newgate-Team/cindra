"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { Team } from "@/lib/types";

import { RequireAuth } from "../components/RequireAuth";

function CreateTeamForm({ onCreated }: { onCreated: (team: Team) => void }) {
  const { token, user } = useAuth();
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (user?.role !== "agency") {
    return (
      <div className="card">
        <p className="muted">
          Команды доступны только агентским аккаунтам. Смените тип аккаунта на{" "}
          <Link href="/settings">странице настроек</Link>, чтобы создать команду.
        </p>
        <p className="muted">
          Если вас пригласили в существующую команду — перейдите по ссылке из письма-приглашения.
        </p>
      </div>
    );
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const team = await api.post<Team>("/team", { name }, token);
      onCreated(team);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось создать команду");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="card">
      <p className="muted">У вас пока нет команды.</p>
      <form onSubmit={handleSubmit}>
        <label>
          Название команды
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="например, Агентство «Восход»"
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={submitting}>
          {submitting ? "Создаём…" : "Создать команду"}
        </button>
      </form>
    </div>
  );
}

function InviteForm({ onInvited }: { onInvited: () => void }) {
  const { token } = useAuth();
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await api.post("/team/invites", { email }, token);
      setSent(true);
      setEmail("");
      onInvited();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось отправить приглашение");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <label>
        Email участника
        <input
          type="email"
          required
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
            setSent(false);
          }}
        />
      </label>
      {error && <p className="error">{error}</p>}
      {sent && <p className="muted">Приглашение отправлено.</p>}
      <button type="submit" disabled={submitting}>
        {submitting ? "Отправляем…" : "Пригласить"}
      </button>
    </form>
  );
}

function TeamView({ team, onChanged }: { team: Team; onChanged: (team: Team) => void }) {
  const { token, user } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const isOwner = team.owner_user_id === user?.id;

  async function refresh() {
    try {
      onChanged(await api.get<Team>("/team", token));
    } catch {
      // Best-effort refresh -- the mutation itself already reported
      // its own error if it failed.
    }
  }

  async function removeMember(memberId: string) {
    setError(null);
    try {
      await api.delete(`/team/members/${memberId}`, token);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось удалить участника");
    }
  }

  return (
    <>
      <div className="card">
        <div className="tile-header-body">
          <strong>{team.name}</strong>
        </div>
        {error && <p className="error">{error}</p>}
        {team.members.map((member) => (
          <div key={member.id} className="analytics-list-row">
            <span>
              {member.email}
              {member.is_owner && <span className="badge active" style={{ marginLeft: 8 }}>Владелец</span>}
            </span>
            {isOwner && !member.is_owner && (
              <button className="secondary" onClick={() => removeMember(member.id)}>
                Удалить
              </button>
            )}
          </div>
        ))}
      </div>

      {isOwner && (
        <div className="card">
          <div className="tile-header-body">
            <strong>Пригласить участника</strong>
          </div>
          <InviteForm onInvited={refresh} />
        </div>
      )}
    </>
  );
}

function TeamPageContent() {
  const { token } = useAuth();
  const [team, setTeam] = useState<Team | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<Team>("/team", token)
      .then(setTeam)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 404) {
          setTeam(null);
        } else {
          setError(err instanceof ApiError ? err.message : "Не удалось загрузить команду");
        }
      });
  }, [token]);

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Команда</h1>
          <p className="muted">Общие соцсети, публикации и генерации для агентства</p>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {team === undefined && !error && <p className="muted">Загрузка…</p>}
      {team === null && <CreateTeamForm onCreated={setTeam} />}
      {team && <TeamView team={team} onChanged={setTeam} />}
    </>
  );
}

export default function TeamPage() {
  return (
    <RequireAuth>
      <TeamPageContent />
    </RequireAuth>
  );
}
