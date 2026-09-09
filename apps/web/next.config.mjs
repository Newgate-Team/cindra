import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  outputFileTracingRoot: __dirname,
  // No CSP here yet -- this app loads scripts/images from several
  // external origins (Google Identity Services, R2-hosted media, the
  // API backend) that would need to be enumerated carefully first;
  // getting that wrong silently breaks login or media rendering, which
  // is worse than the current gap. These four are safe with zero risk
  // of breaking existing functionality (verified: Google Sign-In renders
  // its iframe *inside* our page, so X-Frame-Options on our own
  // responses doesn't affect it).
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Strict-Transport-Security",
            value: "max-age=63072000; includeSubDomains; preload",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
