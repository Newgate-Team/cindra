import type { MetadataRoute } from "next";

import { PUBLIC_PATHS, SITE_URL } from "@/lib/site";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: PUBLIC_PATHS,
        // The app itself is behind a login; nothing in it is worth indexing,
        // and the OAuth callbacks carry one-time codes in their URLs.
        disallow: [
          "/generate",
          "/video",
          "/streams",
          "/templates",
          "/ab-tests",
          "/calendar",
          "/feed",
          "/analytics",
          "/social-accounts",
          "/integrations",
          "/team",
          "/billing",
          "/settings",
          "/oauth/",
          "/reset-password",
          "/verify-email",
        ],
      },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
  };
}
