import type { MetadataRoute } from "next";

// Lets the app be installed to a phone's home screen. The icons are the
// generated ones (app/icon.tsx, app/apple-icon.tsx).
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Cindra",
    short_name: "Cindra",
    description: "Студия AI-контента для соцсетей",
    start_url: "/",
    display: "standalone",
    background_color: "#f6f7fb",
    theme_color: "#f6f7fb",
    lang: "ru",
    icons: [
      { src: "/icon", sizes: "64x64", type: "image/png" },
      { src: "/apple-icon", sizes: "180x180", type: "image/png" },
    ],
  };
}
