"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { Subscription, SubscriptionTier } from "@/lib/types";

import { RequireAuth } from "../components/RequireAuth";

const TIER_LABELS: Record<string, string> = {
  free: "Free",
  pro: "Pro ($19/мес)",
  business: "Business ($100/мес)",
};

const PAYPAL_CLIENT_ID = process.env.NEXT_PUBLIC_PAYPAL_CLIENT_ID ?? "";
const PAYPAL_PLAN_IDS: Partial<Record<SubscriptionTier, string>> = {
  pro: process.env.NEXT_PUBLIC_PAYPAL_PRO_PLAN_ID,
  business: process.env.NEXT_PUBLIC_PAYPAL_BUSINESS_PLAN_ID,
};

// PayPal's Buttons SDK attaches itself to window once the script tag
// loads -- no npm package involved (CIN-87). Minimal shape for what
// this file actually calls, not the full SDK surface.
interface PayPalSubscriptionActions {
  subscription: {
    create: (options: { plan_id: string; custom_id: string }) => Promise<string>;
  };
}

interface PayPalButtonsConfig {
  createSubscription: (data: unknown, actions: PayPalSubscriptionActions) => Promise<string>;
  onApprove: (data: { subscriptionID: string }) => void;
  onError?: (err: unknown) => void;
}

declare global {
  interface Window {
    paypal?: {
      Buttons: (config: PayPalButtonsConfig) => { render: (container: HTMLElement) => void };
    };
  }
}

let paypalSdkPromise: Promise<void> | null = null;

// Loaded once per page, reused across button instances -- vault=true
// and intent=subscription are required for actions.subscription.create
// to be available at all (confirmed via developer.paypal.com/docs/
// subscriptions/integrate, not guessed).
function loadPayPalSdk(): Promise<void> {
  if (window.paypal) return Promise.resolve();
  if (paypalSdkPromise) return paypalSdkPromise;

  paypalSdkPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `https://www.paypal.com/sdk/js?client-id=${PAYPAL_CLIENT_ID}&vault=true&intent=subscription`;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Не удалось загрузить PayPal SDK"));
    document.body.appendChild(script);
  });
  return paypalSdkPromise;
}

function UpgradeButton({
  tier,
  userId,
  onConfirmed,
}: {
  tier: "pro" | "business";
  userId: string;
  onConfirmed: () => void;
}) {
  const { token } = useAuth();
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const planId = PAYPAL_PLAN_IDS[tier];

  useEffect(() => {
    if (!planId || !containerRef.current) return;
    let cancelled = false;

    loadPayPalSdk()
      .then(() => {
        if (cancelled || !containerRef.current || !window.paypal) return;
        window.paypal
          .Buttons({
            createSubscription: (_data, actions) =>
              actions.subscription.create({ plan_id: planId, custom_id: userId }),
            onApprove: async (data) => {
              try {
                await api.post(
                  "/billing/paypal/confirm-subscription",
                  { subscription_id: data.subscriptionID },
                  token
                );
                onConfirmed();
              } catch (err) {
                setError(
                  err instanceof ApiError ? err.message : "Не удалось подтвердить подписку"
                );
              }
            },
            onError: () => setError("Ошибка PayPal — попробуйте ещё раз"),
          })
          .render(containerRef.current);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Ошибка загрузки PayPal"));

    return () => {
      cancelled = true;
    };
  }, [planId, userId, token, onConfirmed]);

  if (!planId) {
    return <p className="muted">Тариф временно недоступен для оплаты.</p>;
  }

  return (
    <div>
      <div ref={containerRef} />
      {error && <p className="error">{error}</p>}
    </div>
  );
}

function BillingSummary() {
  const { token, user } = useAuth();
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [error, setError] = useState<string | null>(null);

  function reload() {
    api
      .get<Subscription>("/billing/subscription", token)
      .then(setSubscription)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Не удалось загрузить"));
  }

  useEffect(reload, [token]);

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
        </div>
      )}

      {subscription && subscription.tier !== "business" && user && (
        <>
          <h2>Апгрейд</h2>
          {subscription.tier === "free" && (
            <div className="card">
              <p>
                <strong>{TIER_LABELS.pro}</strong> — 300 текстов, 60 фото, 6 видео в месяц, без
                лимита публикаций и подключённых аккаунтов.
              </p>
              <UpgradeButton tier="pro" userId={user.id} onConfirmed={reload} />
            </div>
          )}
          <div className="card">
            <p>
              <strong>{TIER_LABELS.business}</strong> — 600 текстов, 150 фото, 55 видео в месяц.
            </p>
            <UpgradeButton tier="business" userId={user.id} onConfirmed={reload} />
          </div>
        </>
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
