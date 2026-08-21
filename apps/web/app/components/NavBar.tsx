"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { useAuth } from "@/lib/auth-context";

import { CalendarIcon, CardIcon, ClapperboardIcon, FeedIcon, ShareIcon, WandIcon } from "./icons";

const NAV_LINKS = [
  { href: "/generate", label: "Посты", icon: WandIcon },
  { href: "/video", label: "Видео", icon: ClapperboardIcon },
  { href: "/calendar", label: "Календарь", icon: CalendarIcon },
  { href: "/feed", label: "Лента", icon: FeedIcon },
  { href: "/social-accounts", label: "Соцсети", icon: ShareIcon },
  { href: "/billing", label: "Тариф", icon: CardIcon },
];

function Logo() {
  return (
    <Link href="/" className="brand">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src="/logo-icon.png" alt="" width={28} height={28} />
      <span>cindra</span>
    </Link>
  );
}

export function NavBar() {
  const { user, loading, logout } = useAuth();
  const router = useRouter();

  function handleLogout() {
    logout();
    router.push("/login");
  }

  return (
    <>
      {/* Desktop: fixed left sidebar. Mobile: top bar, see .topbar below. */}
      <aside className="sidebar">
        <Logo />
        {user && (
          <nav className="sidebar-links">
            {NAV_LINKS.map(({ href, label, icon: Icon }) => (
              <Link key={href} href={href}>
                <Icon />
                {label}
              </Link>
            ))}
          </nav>
        )}
        <div className="sidebar-footer">
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
        </div>
      </aside>

      <header className="topbar">
        <Logo />
        {!loading &&
          (user ? (
            <button className="secondary" onClick={handleLogout}>
              Выйти
            </button>
          ) : (
            <span className="topbar-auth-links">
              <Link href="/login">Войти</Link>
              <Link href="/register">Регистрация</Link>
            </span>
          ))}
      </header>

      {user && (
        <nav className="bottom-tabs">
          {NAV_LINKS.map(({ href, label, icon: Icon }) => (
            <Link key={href} href={href}>
              <Icon />
              <span>{label}</span>
            </Link>
          ))}
        </nav>
      )}
    </>
  );
}
