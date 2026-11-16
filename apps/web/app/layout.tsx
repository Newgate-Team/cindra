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


export const metadata = {
  title: "Cindra",
  description: "AI-контент для соцсетей",
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
