"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { Subscription, SubscriptionTier } from "@/lib/types";

import { RequireAuth } from "../components/RequireAuth";

const TIER_LABELS: Record<string, string> = {
  free: "Free",
  pro: "Pro ($19/мес)",
  business: "Business ($100/мес)",
};

// Grid rendering for the tier-comparison cards below -- kept as
// separate lookup tables (rather than reusing/reparsing TIER_LABELS)
// so the numbers stay a direct, readable mirror of app/plans.py's
// real PLAN_LIMITS (free: 20 text/3 image/0 video, 10 publications,
// 1 account; pro/business already matched what the upgrade copy
// below said before this page was restyled).
const TIER_ORDER: SubscriptionTier[] = ["free", "pro", "business"];
const TIER_NAME: Record<SubscriptionTier, string> = { free: "Free", pro: "Pro", business: "Business" };
const TIER_PRICE: Record<SubscriptionTier, string> = { free: "0 $", pro: "19 $", business: "100 $" };
const TIER_FEATURES: Record<SubscriptionTier, string[]> = {
  free: ["20 текстов, 3 фото в месяц", "10 публикаций в месяц", "1 подключённый аккаунт"],
  pro: ["300 текстов, 60 фото, 6 видео в месяц", "Без лимита публикаций", "Без лимита аккаунтов"],
  business: ["600 текстов, 150 фото, 55 видео в месяц", "Без лимита публикаций", "Без лимита аккаунтов"],
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
    // Belt-and-suspenders against CIN-88: paypal.Buttons().render()
    // appends to the container rather than replacing its content, so
    // if this effect ever re-runs against an already-populated
    // container (e.g. an unmemoized onConfirmed reference changing on
    // every parent render) we'd otherwise get a second set of buttons
    // stacked on top of the first.
    containerRef.current.innerHTML = "";

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

  // Memoized so it's a stable reference across renders (CIN-88) --
  // UpgradeButton's useEffect depends on it, and an unmemoized
  // function here would re-run that effect (and re-render the PayPal
  // buttons on top of themselves) on every unrelated re-render of
  // this component, not just when something the user did should
  // actually refresh the subscription.
  const reload = useCallback(() => {
    api
      .get<Subscription>("/billing/subscription", token)
      .then(setSubscription)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Не удалось загрузить"));
  }, [token]);

  useEffect(reload, [reload]);

  const currentIndex = subscription ? TIER_ORDER.indexOf(subscription.tier) : -1;

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Тариф</h1>
          {subscription && (
            <p className="muted">
              {TIER_LABELS[subscription.tier] ?? subscription.tier}
              {subscription.current_period_end &&
                ` · продление ${new Date(subscription.current_period_end).toLocaleDateString("ru-RU")}`}
            </p>
          )}
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {subscription && (
        <>
          <p>
            Статус: <span className={`badge ${subscription.status}`}>{subscription.status}</span>
          </p>

          <div className="tile-grid">
            {TIER_ORDER.map((tier) => {
              const isCurrent = subscription.tier === tier;
              const tierIndex = TIER_ORDER.indexOf(tier);
              return (
                <div key={tier} className={`card tier-card${isCurrent ? " current" : ""}`}>
                  <div className="tile-header">
                    <div className="tile-header-body">
                      <strong>{TIER_NAME[tier]}</strong>
                    </div>
                    {isCurrent && <span className="badge active">Текущий</span>}
                  </div>
                  <p className="tier-price">
                    {TIER_PRICE[tier]}
                    <span>/мес</span>
                  </p>
                  <ul className="tier-features">
                    {TIER_FEATURES[tier].map((feature) => (
                      <li key={feature}>{feature}</li>
                    ))}
                  </ul>
                  {isCurrent ? (
                    <button className="secondary" disabled>
                      Активен
                    </button>
                  ) : (
                    tierIndex > currentIndex &&
                    user &&
                    (tier === "pro" || tier === "business") && (
                      <UpgradeButton tier={tier} userId={user.id} onConfirmed={reload} />
                    )
                  )}
                </div>
              );
            })}
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
