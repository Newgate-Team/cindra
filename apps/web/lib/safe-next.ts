/** Where to go after logging in: only a path on this site. An absolute
 * or protocol-relative ?next= (https://evil.example, //evil.example,
 * /\evil.example) would turn the login page into an open redirect --
 * the classic "log in, land on a lookalike site" phishing step.
 *
 * Checked by resolving it the way the router will, not by its prefix:
 * the URL parser drops tabs/newlines anywhere and reads "\" as "/", so
 * ?next=/%09/evil.example passed a "//"-prefix check and still resolved
 * to //evil.example, which Next.js then navigates to as an external URL. */
const BASE = "http://cindra.invalid";

export function safeNext(next: string | null, fallback = "/generate"): string {
  if (!next) return fallback;
  let url: URL;
  try {
    url = new URL(next, BASE);
  } catch {
    return fallback;
  }
  if (url.origin !== BASE) return fallback;
  // The normalized form -- exactly what was checked is what gets opened.
  return url.pathname + url.search + url.hash;
}
