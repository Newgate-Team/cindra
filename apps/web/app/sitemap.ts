import type { MetadataRoute } from "next";

import { PUBLIC_PATHS, SITE_URL } from "@/lib/site";

export default function sitemap(): MetadataRoute.Sitemap {
  return PUBLIC_PATHS.map((route) => ({
    url: `${SITE_URL}${route === "/" ? "" : route}`,
    changeFrequency: route === "/" ? "weekly" : "yearly",
    priority: route === "/" ? 1 : route === "/register" ? 0.8 : 0.4,
  }));
}
