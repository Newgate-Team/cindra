/** Where to go after logging in: only a path on this site. An absolute
 * or protocol-relative ?next= (https://evil.example, //evil.example,
 * /\evil.example) would turn the login page into an open redirect --
 * the classic "log in, land on a lookalike site" phishing step. */
export function safeNext(next: string | null, fallback = "/generate"): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) {
    return fallback;
  }
  return next;
}
