"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { UserRole } from "@/lib/types";

export default function RegisterPage() {
  const { register } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<UserRole>("solo");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await register(email, password, role);
      router.push("/generate");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось зарегистрироваться");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <h1>Регистрация</h1>
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
          Пароль (минимум 8 символов)
          <input
            type="password"
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <label>
          Я —
          <select value={role} onChange={(e) => setRole(e.target.value as UserRole)}>
            <option value="solo">Соло-предприниматель / крео</option>
            <option value="agency">Агентство</option>
          </select>
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={submitting}>
          {submitting ? "Создаём аккаунт…" : "Зарегистрироваться"}
        </button>
      </form>
    </>
  );
}
