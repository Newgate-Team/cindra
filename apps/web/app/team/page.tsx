"use client";

import { Users } from "lucide-react";
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
      <div className="panel empty">
        <span className="tile" aria-hidden="true">
          <Users size={26} />
        </span>
        <h2>Команды — для агентств</h2>
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
    <div className="panel settings-stack">
      <h2>Создайте команду</h2>
      <p className="muted">У вас пока нет команды — общие соцсети, публикации и генерации появятся после создания.</p>
      <form onSubmit={handleSubmit} className="dialog-form">
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
    <form onSubmit={handleSubmit} className="dialog-form">
      <label className="field-block">
        <span className="field-label">Email участника</span>
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
      <section className="panel">
        <div className="section-head">
          <h2>{team.name}</h2>
          <span className="tag">{team.members.length} уч.</span>
        </div>
        {error && <p className="error">{error}</p>}
        {team.members.map((member) => (
          <div key={member.id} className="list-line">
            <span className="avatar" aria-hidden="true">
              {member.email.charAt(0).toUpperCase()}
            </span>
            <div className="list-line-text">
              <b>{member.email}</b>
            </div>
            {member.is_owner ? (
              <span className="tag purple">Владелец</span>
            ) : (
              <span className="tag">Участник</span>
            )}
            {isOwner && !member.is_owner && (
              <button type="button" className="ghost" onClick={() => removeMember(member.id)}>
                Удалить
              </button>
            )}
          </div>
        ))}
      </section>

      {isOwner && (
        <section className="panel section">
          <h2>Пригласить участника</h2>
          <InviteForm onInvited={refresh} />
        </section>
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
          <h1>Создавать вместе</h1>
          <p className="muted">Общие площадки, идеи и публикации вашей команды.</p>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {team === undefined && !error && <div className="skeleton" aria-busy="true" />}
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
