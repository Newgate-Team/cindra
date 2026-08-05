import "./globals.css";

import { AuthProvider } from "@/lib/auth-context";

import { Footer } from "./components/Footer";
import { NavBar } from "./components/NavBar";

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
          <NavBar />
          <main>{children}</main>
          <Footer />
        </AuthProvider>
      </body>
    </html>
  );
}
