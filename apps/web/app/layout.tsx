// Self-hosted via Fontsource (npm) instead of next/font/google (CIN-131):
// the latter fetches woff2 files from fonts.gstatic.com at *build* time,
// which the GitHub Actions runner intermittently can't reach, hard-
// failing the whole build. Fontsource bundles the actual font files in
// the npm package itself, so the build only ever depends on the npm
// registry, which every CI run this session has reached reliably.
// Each weight file below already includes latin + cyrillic (and other)
// unicode-range subsets in one file -- the browser only fetches the
// subset it actually needs per character on the page.
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-sans/700.css";
import "./globals.css";

import { AuthProvider } from "@/lib/auth-context";

import { Footer } from "./components/Footer";
import { NavBar } from "./components/NavBar";

export const metadata = {
  title: "Cindra",
  description: "AI-контент для соцсетей",
  // app.cindra.online — приватный кабинет, не публичный сайт (CRW-13):
  // индексироваться должен только будущий маркетинговый cindra.online.
  // Дублируется в app/robots.ts — meta-тег закрывает страницы, которые
  // краулер уже знает, robots.txt останавливает обход новых.
  robots: { index: false, follow: false },
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
          <div className="app-shell">
            <NavBar />
            <div className="app-content">
              <main>{children}</main>
              <Footer />
            </div>
          </div>
        </AuthProvider>
      </body>
    </html>
  );
}
