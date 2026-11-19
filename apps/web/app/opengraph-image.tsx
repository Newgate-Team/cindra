import { readFile } from "node:fs/promises";
import path from "node:path";

import { ImageResponse } from "next/og";

// The card that shows when the landing page is shared in a chat or a
// feed. Fonts are read from the Fontsource package already in
// node_modules (Satori needs woff/ttf, and Cyrillic lives in its own
// subset file) -- no network fetch at build time (CIN-131).
export const alt = "Cindra — весь контент для соцсетей в одной студии";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

async function font(subset: "latin" | "cyrillic") {
  const file = path.join(
    process.cwd(),
    "node_modules/@fontsource/manrope/files",
    `manrope-${subset}-800-normal.woff`
  );
  return readFile(file);
}

export default async function OpenGraphImage() {
  const [latin, cyrillic] = await Promise.all([font("latin"), font("cyrillic")]);
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: 72,
          background: "linear-gradient(120deg, #fff6ee, #fff0f2 62%, #f7f0ff)",
          color: "#14161f",
          fontFamily: "Manrope, ManropeCyrillic",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
          <div
            style={{
              width: 76,
              height: 76,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              borderRadius: 22,
              background: "linear-gradient(135deg, #ff8a3d, #ff4d6d)",
            }}
          >
            <svg width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 3q1 4 4 6.5t3 5.5a1 1 0 0 1-14 0 5 5 0 0 1 1-3 1 1 0 0 0 5 0c0-2-1.5-3-1.5-5q0-2 2.5-4" />
            </svg>
          </div>
          <div style={{ fontSize: 52 }}>cindra.</div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <div style={{ display: "flex", flexDirection: "column", fontSize: 92, lineHeight: 1.05, letterSpacing: -2 }}>
            <div>Весь контент.</div>
            <div>Одна студия.</div>
          </div>
          <div style={{ fontSize: 34, color: "#626879", fontWeight: 600 }}>
            Посты, видео, эфир и публикации — от идеи до всех ваших площадок.
          </div>
        </div>
      </div>
    ),
    {
      ...size,
      fonts: [
        { name: "Manrope", data: latin, weight: 800, style: "normal" },
        { name: "ManropeCyrillic", data: cyrillic, weight: 800, style: "normal" },
      ],
    }
  );
}
