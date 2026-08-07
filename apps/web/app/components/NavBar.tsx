"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { useAuth } from "@/lib/auth-context";

export function NavBar() {
  const { user, loading, logout } = useAuth();
  const router = useRouter();

  function handleLogout() {
    logout();
    router.push("/login");
  }

  return (
    <nav>
      <Link href="/">Cindra</Link>
      {user && (
        <>
          <Link href="/generate">Генерация</Link>
          <Link href="/calendar">Календарь</Link>
          <Link href="/feed">Лента</Link>
          <Link href="/social-accounts">Соцсети</Link>
          <Link href="/billing">Тариф</Link>
        </>
      )}
      <span className="spacer" />
      {loading ? null : user ? (
        <>
          <span className="muted">{user.email}</span>
          <button className="secondary" onClick={handleLogout}>
            Выйти
          </button>
        </>
      ) : (
        <>
          <Link href="/login">Войти</Link>
          <Link href="/register">Регистрация</Link>
        </>
      )}
    </nav>
  );
}
