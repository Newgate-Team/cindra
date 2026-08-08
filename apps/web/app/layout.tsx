import { IBM_Plex_Sans } from "next/font/google";

import "./globals.css";

import { AuthProvider } from "@/lib/auth-context";

import { Footer } from "./components/Footer";
import { NavBar } from "./components/NavBar";

const ibmPlexSans = IBM_Plex_Sans({
  subsets: ["latin", "cyrillic"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-sans",
  display: "swap",
});

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
    <html lang="ru" className={ibmPlexSans.variable}>
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
