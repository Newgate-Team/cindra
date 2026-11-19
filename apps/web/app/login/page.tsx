"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, type FormEvent } from "react";

import { AuthLayout } from "@/app/components/AuthLayout";
import GoogleSignInButton from "@/app/components/GoogleSignInButton";
import { ApiError } from "@/lib/api";
import { SESSION_EXPIRED_KEY, useAuth } from "@/lib/auth-context";
import { safeNext } from "@/lib/safe-next";

function LoginForm() {
  const { login } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  // e.g. a team-invite link sends a logged-out visitor here first
  // (app/team/accept/page.tsx) -- ?next= brings them back to finish
  // what they came for instead of always landing on /generate.
  const next = safeNext(searchParams.get("next"));
  // Read after mount, not while rendering: sessionStorage only exists in
  // the browser, so reading it in the initial state made the server HTML
  // (no message) differ from the client's (message shown) -- a hydration
  // mismatch whenever the session had just expired.
  const [expired, setExpired] = useState(false);
  useEffect(() => {
    try {
      if (sessionStorage.getItem(SESSION_EXPIRED_KEY) === "1") {
        sessionStorage.removeItem(SESSION_EXPIRED_KEY);
        setExpired(true);
      }
    } catch {
      // Private mode / blocked storage: no message, nothing to recover.
    }
  }, []);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      router.push(next);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось войти");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout
      title="Рады видеть вас снова"
      subtitle="Войдите, чтобы продолжить работу."
      poster="Ваша следующая большая идея."
      alt={{ text: "Впервые здесь?", href: "/register", label: "Создать аккаунт" }}
    >
      {expired && <p className="muted">Сессия истекла — войдите снова, и вы вернётесь туда, где были.</p>}
      <form onSubmit={handleSubmit}>
        <label>
          Email
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label>
          Пароль
          <input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={submitting}>
          {submitting ? "Входим…" : "Войти"}
        </button>
      </form>
      <p className="muted">
        <Link href="/forgot-password">Забыли пароль?</Link>
      </p>
      <GoogleSignInButton onError={setError} next={next} />
    </AuthLayout>
  );
}

// useSearchParams() needs a Suspense boundary above it in the App
// Router (same fix as /reset-password, /verify-email).
export default function LoginPage() {
  return (
    <Suspense fallback={<p className="muted">Загрузка…</p>}>
      <LoginForm />
    </Suspense>
  );
}
