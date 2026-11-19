"use client";

import { useAuth } from "@/lib/auth-context";

import { Dashboard } from "./components/Dashboard";
import { Landing } from "./components/Landing";

// One address, two faces: the studio's overview for people who are
// signed in, the landing page for everyone else. (It used to redirect
// signed-in users straight into «Посты».)
export default function Home() {
  const { user, loading } = useAuth();

  if (loading) return null;
  return user ? <Dashboard /> : <Landing />;
}
