"use client";

import { useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { Subscription } from "@/lib/types";

import { RequireAuth } from "../components/RequireAuth";

const TIER_LABELS: Record<string, string> = {
  free: "Free",
  pro: "Pro ($19/мес)",
  business: "Business ($100/мес)",
};

function BillingSummary() {
  const { token } = useAuth();
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<Subscription>("/billing/subscription", token)
      .then(setSubscription)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Не удалось загрузить"));
  }, [token]);

  return (
    <>
      <h1>Тариф</h1>
      {error && <p className="error">{error}</p>}
      {subscription && (
        <div className="card">
          <p>
            Текущий тариф: <strong>{TIER_LABELS[subscription.tier] ?? subscription.tier}</strong>{" "}
            <span className={`badge ${subscription.status}`}>{subscription.status}</span>
          </p>
          {subscription.current_period_end && (
            <p className="muted">
              Продлится до {new Date(subscription.current_period_end).toLocaleDateString("ru-RU")}
            </p>
          )}
          {subscription.tier === "free" && (
            <p className="muted">
              На бесплатном тарифе — 20 текстов, 3 фото и 10 публикаций в месяц (видео
              недоступно). Апгрейд до Pro/Business пока недоступен: не выбран платёжный провайдер
              (см. задачу CIN-18).
            </p>
          )}
        </div>
      )}
    </>
  );
}

export default function BillingPage() {
  return (
    <RequireAuth>
      <BillingSummary />
    </RequireAuth>
  );
}
