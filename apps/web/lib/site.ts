// Where the app lives, for absolute URLs in metadata, the sitemap and
// robots.txt. Set NEXT_PUBLIC_SITE_URL in the deployment; the default
// is the current production host.
export const SITE_URL = (process.env.NEXT_PUBLIC_SITE_URL ?? "https://cindra-chi.vercel.app").replace(/\/$/, "");

// Pages anyone (and search engines) may see. Everything else sits
// behind a login and is kept out of the index.
export const PUBLIC_PATHS = ["/", "/login", "/register", "/privacy", "/terms", "/data-deletion"];
