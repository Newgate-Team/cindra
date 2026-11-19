"use client";

import { Trophy } from "lucide-react";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { ABTest, SocialAccount } from "@/lib/types";

import { RequireAuth } from "../../components/RequireAuth";
import { ReviewAndPublish } from "../../components/ReviewAndPublish";

const TERMINAL_STATUSES = new Set(["completed", "failed", "flagged"]);
const POLL_INTERVAL_MS = 2000;

const STATUS_LABELS: Record<string, string> = {
  queued: "В очереди",
  processing: "Генерируется…",
  completed: "Готово",
  failed: "Ошибка",
  flagged: "Отклонено модерацией",
};

function ABTestView() {
  const { token } = useAuth();
  const params = useParams();
  const testId = params.id as string;

  const [test, setTest] = useState<ABTest | null>(null);
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [settingWinner, setSettingWinner] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    api.get<SocialAccount[]>("/social-accounts", token).then(setAccounts);
  }, [token]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const updated = await api.get<ABTest>(`/ab-tests/${testId}`, token);
        if (cancelled) return;
        setTest(updated);
        const allTerminal = updated.variants.every((v) => TERMINAL_STATUSES.has(v.status));
        if (allTerminal && pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Не удалось загрузить тест");
        if (pollRef.current) clearInterval(pollRef.current);
      }
    }

    load();
    pollRef.current = setInterval(load, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [testId, token]);

  async function pickWinner(generationJobId: string) {
    setSettingWinner(generationJobId);
    try {
      const updated = await api.post<ABTest>(
        `/ab-tests/${testId}/winner`,
        { generation_job_id: generationJobId },
        token
      );
      setTest(updated);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось выбрать вариант");
    } finally {
      setSettingWinner(null);
    }
  }

  if (error) return <p className="error">{error}</p>;
  if (!test) return <div className="skeleton" aria-busy="true" />;

  const winner = test.variants.find((v) => v.id === test.winner_generation_job_id) ?? null;

  const letters = "ABCDEFGH";

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Найдите сильную идею</h1>
          <p className="muted">{test.topic}</p>
        </div>
      </div>

      <div className={`variant-cols n${test.variants.length}`}>
        {test.variants.map((variant, index) => {
          const isWinner = variant.id === test.winner_generation_job_id;
          return (
            <section key={variant.id} className={`panel variant${isWinner ? " winner" : ""}`}>
              <div className="section-head">
                <span className="variant-letter" aria-hidden="true">
                  {letters[index] ?? index + 1}
                </span>
                {isWinner ? (
                  <span className="tag green">
                    <Trophy size={12} aria-hidden="true" /> Победитель
                  </span>
                ) : (
                  <span className={`badge ${variant.status}`}>{STATUS_LABELS[variant.status] ?? variant.status}</span>
                )}
              </div>
              {variant.status === "completed" && (
                <>
                  <p className="variant-text">{variant.output_payload?.text}</p>
                  {!isWinner && (
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => pickWinner(variant.id)}
                      disabled={settingWinner !== null}
                    >
                      {settingWinner === variant.id ? "Выбираем…" : `Выбрать вариант ${letters[index] ?? index + 1}`}
                    </button>
                  )}
                </>
              )}
              {(variant.status === "queued" || variant.status === "processing") && (
                <div className="skeleton" aria-busy="true" />
              )}
              {(variant.status === "failed" || variant.status === "flagged") && (
                <p className="error">{variant.error_message}</p>
              )}
            </section>
          );
        })}
      </div>

      {winner && winner.output_payload?.text && (
        <section className="panel section">
          <h2>Опубликовать победителя</h2>
          <ReviewAndPublish
            generatedText={winner.output_payload.text}
            generationJobId={winner.id}
            contentKind="post"
            initialCaption={winner.output_payload.text}
            accounts={accounts}
            targetAccountIds={[]}
          />
        </section>
      )}
    </>
  );
}

export default function ABTestPage() {
  return (
    <RequireAuth>
      <ABTestView />
    </RequireAuth>
  );
}
