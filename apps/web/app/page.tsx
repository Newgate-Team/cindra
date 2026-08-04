"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/lib/auth-context";

export default function Home() {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && user) router.push("/generate");
  }, [loading, user, router]);

  if (loading || user) return null;

  return (
    <>
      <h1>Cindra</h1>
      <p>AI-контент для соцсетей: тема → готовый пост → публикация.</p>
      <p>
        <Link href="/register">Зарегистрироваться</Link> или <Link href="/login">войти</Link>.
      </p>
    </>
  );
}
