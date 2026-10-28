"""Smoke-test the customer-portal integration (read-only).

Run:  python -m scripts.check_portal --username USER --password PASS
      (username/password default to PORTAL_USERNAME / PORTAL_PASSWORD in .env)

Checks, in order: portal health -> login (cookie token) -> token details ->
appointment list. Optional: --mobile to also test the patient lookup.
Never books anything.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from modules.portal.portal_http_client import get_shared_portal_http_client
from modules.portal.errors import PortalApiError, PortalNetworkError


def _health_build_portal_url(base_url: str) -> str:
    """The portal serves /health at the root (no /api prefix)."""
    trimmed = base_url[:-4] if base_url.endswith("/api") else base_url
    return f"{trimmed.rstrip('/')}/health"


async def main() -> None:
    """Run each integration check and exit non-zero on the first failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default=os.getenv("PORTAL_USERNAME", ""))
    parser.add_argument("--password", default=os.getenv("PORTAL_PASSWORD", ""))
    parser.add_argument("--mobile", default="", help="Also test patient lookup by mobile")
    args = parser.parse_args()

    client = get_shared_portal_http_client()
    base_url = client._base_url  # noqa: SLF001 — smoke script, same package
    print(f"portal base : {base_url}")

    # 1. health (no auth)
    import httpx

    try:
        health = await httpx.AsyncClient().get(_health_build_portal_url(base_url), timeout=5)
        print(f"health      : HTTP {health.status_code}")
    except httpx.HTTPError as error:
        print(f"health      : FAILED — {error}")
        print("Fix: start the portal (PORT 50001) or set PORTAL_BASE_URL in .env.")
        sys.exit(1)

    # 2. login -> cookie token
    if not args.username or not args.password:
        print("login       : SKIPPED (pass --username/--password or set PORTAL_USERNAME/PORTAL_PASSWORD)")
        print("\nPartial check passed (portal reachable).")
        return

    try:
        token = await client.login(args.username, args.password)
    except PortalNetworkError as error:
        print(f"login       : FAILED — {error}")
        sys.exit(1)
    except PortalApiError as error:
        print(f"login       : FAILED — {error}")
        print("Fix: check the credentials (username + password).")
        sys.exit(1)
    print(f"login       : OK (token {token[:12]}…)")

    # 3. token details (guarded endpoint) + active location
    try:
        me = await client.get_token_details(token)
        user = (me.get("data") or {}) if isinstance(me, dict) else {}
        who = user.get("email") or user.get("username") or "signed-in user"
        where = user.get("locationId") or ""
        print(f"token       : OK ({who})" + (f" @ location {where}" if where else ""))
    except PortalApiError as error:
        print(f"token       : FAILED — {error}")
        sys.exit(1)

    # 4. appointment list
    try:
        listing = await client.list_appointments(
            token, {"page": 1, "perPage": 5, "sortKey": "startTime", "sortValue": "desc"}
        )
        data = listing.get("data") or []
        total = (listing.get("meta") or {}).get("totalCount")
        print(f"appointments: OK ({len(data)} rows" + (f", total {total}" if total else "") + ")")
    except PortalApiError as error:
        print(f"appointments: FAILED — {error}")
        sys.exit(1)

    # 5. optional patient lookup
    if args.mobile:
        try:
            envelope = await client.patient_by_mobile(token, args.mobile)
            data = envelope.get("data")
            found = data.get("patientId") if isinstance(data, dict) else None
            print(f"patient     : {'OK (id ' + str(found) + ')' if found else 'not found'}")
        except PortalApiError as error:
            print(f"patient     : FAILED — {error}")
            sys.exit(1)

    print("\nAll checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
