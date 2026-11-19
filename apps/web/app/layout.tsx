// Self-hosted via Fontsource (npm) instead of next/font/google (CIN-131):
// the latter fetches woff2 files from fonts.gstatic.com at *build* time,
// which the GitHub Actions runner intermittently can't reach, hard-
// failing the whole build. Fontsource bundles the actual font files in
// the npm package itself, so the build only ever depends on the npm
// registry, which every CI run this session has reached reliably.
// Each weight file below already includes latin + cyrillic (and other)
// unicode-range subsets in one file -- the browser only fetches the
// subset it actually needs per character on the page.
// Inter for text, Manrope for headings and numbers (the redesign).
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "@fontsource/manrope/600.css";
import "@fontsource/manrope/700.css";
import "@fontsource/manrope/800.css";
import "./globals.css";

import { AuthProvider } from "@/lib/auth-context";

import { AppShell } from "./components/AppShell";
import { EmailVerificationBanner } from "./components/EmailVerificationBanner";
import { Footer } from "./components/Footer";


import type { Metadata, Viewport } from "next";

import { SITE_URL } from "@/lib/site";

const DESCRIPTION =
  "Посты, видео, аватары и эфиры для соцсетей в одной студии: от идеи до публикации на всех ваших площадках.";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: { default: "Cindra — весь контент для соцсетей в одной студии", template: "%s — Cindra" },
  description: DESCRIPTION,
  applicationName: "Cindra",
  openGraph: {
    type: "website",
    siteName: "Cindra",
    locale: "ru_RU",
    title: "Cindra — весь контент для соцсетей в одной студии",
    description: DESCRIPTION,
  },
  twitter: { card: "summary_large_image", title: "Cindra", description: DESCRIPTION },
};

export const viewport: Viewport = {
  themeColor: "#f6f7fb",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ru">
      <body>
        <AuthProvider>
          <AppShell>
            <EmailVerificationBanner />
            <main>{children}</main>
            <Footer />
          </AppShell>
        </AuthProvider>
      </body>
    </html>
  );
}
