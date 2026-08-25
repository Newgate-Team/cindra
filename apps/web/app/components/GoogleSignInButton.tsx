"use client";

import { useRouter } from "next/navigation";
import Script from "next/script";
import { useCallback, useRef } from "react";

import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

const GOOGLE_CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID;

interface GoogleCredentialResponse {
  credential: string;
}

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (config: {
            client_id: string;
            callback: (response: GoogleCredentialResponse) => void;
          }) => void;
          renderButton: (
            parent: HTMLElement,
            options: Record<string, unknown>
          ) => void;
        };
      };
    };
  }
}

export default function GoogleSignInButton({
  onError,
}: {
  onError: (message: string) => void;
}) {
  const { loginWithGoogle } = useAuth();
  const router = useRouter();
  const containerRef = useRef<HTMLDivElement | null>(null);

  const initialize = useCallback(() => {
    if (!GOOGLE_CLIENT_ID || !window.google || !containerRef.current) return;
    window.google.accounts.id.initialize({
      client_id: GOOGLE_CLIENT_ID,
      callback: async (response) => {
        try {
          await loginWithGoogle(response.credential);
          router.push("/generate");
        } catch (err) {
          onError(
            err instanceof ApiError ? err.message : "Не удалось войти через Google"
          );
        }
      },
    });
    window.google.accounts.id.renderButton(containerRef.current, {
      theme: "outline",
      size: "large",
      text: "continue_with",
      locale: "ru",
      width: 320,
    });
  }, [loginWithGoogle, onError, router]);

  // Without a configured client id there is nothing to render -- the
  // page falls back to email/password only.
  if (!GOOGLE_CLIENT_ID) return null;

  return (
    <>
      {/* next/script dedupes by src, and onReady fires on every mount
          (unlike onLoad) -- so navigating login <-> register re-renders
          the button without reloading the GIS script. */}
      <Script
        src="https://accounts.google.com/gsi/client"
        strategy="afterInteractive"
        onReady={initialize}
      />
      <div className="google-signin" ref={containerRef} />
    </>
  );
}
