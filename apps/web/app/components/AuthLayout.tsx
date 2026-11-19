import Link from "next/link";
import type { ReactNode } from "react";

import { Poster } from "./Brand";

// Sign-in and sign-up: the form on a white panel, a poster beside it
// (it drops below the form on a phone). `alt` is the link to the other page.
export function AuthLayout({
  title,
  subtitle,
  poster,
  alt,
  children,
}: {
  title: string;
  subtitle: string;
  poster: string;
  alt: { text: string; href: string; label: string };
  children: ReactNode;
}) {
  return (
    <div className="auth-layout">
      <section className="panel">
        <h1>{title}</h1>
        <p className="muted">{subtitle}</p>
        {children}
        <p className="muted auth-alt">
          {alt.text} <Link href={alt.href}>{alt.label}</Link>
        </p>
      </section>
      <Poster title={poster} tone="sun" />
    </div>
  );
}
