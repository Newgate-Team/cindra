"""Read-only smoke test of a deployed environment.

    python devtools/smoke.py --api https://api.example.com --web https://app.example.com

Creates and changes nothing: a few GETs, one CORS preflight and one login
attempt with an address that cannot exist. Exit code 1 if anything is wrong,
so it can gate a release.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request


def _call(url: str, method: str = "GET", headers: dict | None = None, body: bytes | None = None):
    request = urllib.request.Request(url, method=method, headers=headers or {}, data=body)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()
    except urllib.error.URLError as err:
        return 0, {}, str(err.reason).encode()


class Report:
    def __init__(self) -> None:
        self.failed = 0

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        print(f"{'ok  ' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""))
        self.failed += 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--api", required=True, help="base URL of the API")
    parser.add_argument("--web", help="base URL of the web app")
    parser.add_argument("--origin", help="origin the browser calls from (default: --web)")
    args = parser.parse_args()
    api = args.api.rstrip("/")
    web = (args.web or "").rstrip("/")
    origin = args.origin or web
    report = Report()

    status, _, _ = _call(f"{api}/health")
    report.check("API is alive (/health)", status == 200, f"HTTP {status}")

    status, _, body = _call(f"{api}/health/ready")
    try:
        checks = json.loads(body).get("checks", {})
    except ValueError:
        checks = {}
    report.check("database reachable (/health/ready)", status == 200 and checks.get("database") == "ok", f"HTTP {status} {checks}")
    report.check("redis reachable (queues, rate limits)", checks.get("redis") == "ok", str(checks.get("redis")))

    if origin:
        status, headers, _ = _call(
            f"{api}/auth/login",
            "OPTIONS",
            {
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        allowed = {k.lower(): v for k, v in headers.items()}.get("access-control-allow-origin")
        report.check(f"CORS allows {origin}", allowed in (origin, "*"), f"HTTP {status}, allow-origin={allowed}")

    status, headers, _ = _call(
        f"{api}/auth/login",
        "POST",
        {"Content-Type": "application/json"},
        json.dumps({"email": "smoke-test@invalid.example", "password": "not-a-real-password"}).encode(),
    )
    report.check("login route answers 401/422/429 for unknown user", status in (401, 422, 429), f"HTTP {status}")
    lowered = {k.lower(): v for k, v in headers.items()}
    report.check("responses carry X-Request-ID", "x-request-id" in lowered)

    status, _, _ = _call(f"{api}/posts")
    report.check("protected routes reject anonymous calls", status == 401, f"HTTP {status}")

    if web:
        status, headers, body = _call(web)
        report.check("web app loads", status == 200, f"HTTP {status}")
        lowered = {k.lower(): v for k, v in headers.items()}
        report.check("HSTS header set on the web app", "strict-transport-security" in lowered)
        status, _, body = _call(f"{web}/robots.txt")
        report.check("robots.txt served", status == 200 and b"Sitemap" in body, f"HTTP {status}")
        status, _, _ = _call(f"{web}/login")
        report.check("login page loads", status == 200, f"HTTP {status}")

    print("\nAll checks passed." if not report.failed else f"\n{report.failed} check(s) failed.")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
