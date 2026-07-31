"""Can a seller's own counters tell us WHY a listing vanished?

The item page is unreachable, so the distinction has to come from somewhere
else. A sale changes the seller, not just the listing: Vinted user objects carry
counters like item_count and feedback counts. If a "sold" counter exists and
increments when a listing disappears, that separates SOLD from HIDDEN/REMOVED
using only small JSON calls on the unblocked API.

Also retries the Next.js data endpoint, since the buildId regex may simply have
missed on an escaped page.
"""

import json
import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vintedwatch.client import BASE, VintedClient  # noqa: E402

SELLERS = {32001697: "sold 9505849905", 3138688371: "sold 9493035670"}
COUNTERISH = ("count", "feedback", "given", "taken", "sold", "item")


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(message)s")
    state = json.load(open("data/state.json"))
    client = VintedClient(token_cache=state.get("token"))

    print("=== 1. what does /api/v2/users/{id} expose? ===")
    for uid, note in SELLERS.items():
        r = client._get(f"{BASE}/api/v2/users/{uid}", tries=2,
                        headers={"Accept": "application/json", "Referer": BASE + "/"})
        if r is None or r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
            print(f"  user {uid} -> HTTP {r.status_code if r is not None else 'none'}")
            continue
        user = r.json().get("user", r.json())
        hits = {k: v for k, v in user.items()
                if any(t in k for t in COUNTERISH) and isinstance(v, (int, float, str))}
        print(f"  user {uid} ({note}), {len(r.content)//1024} KB")
        print(f"      {json.dumps(hits, ensure_ascii=False)[:420]}")

    print("\n=== 2. does the wardrobe response carry the user object too? ===")
    uid = next(iter(SELLERS))
    r = client._get(f"{BASE}/api/v2/wardrobe/{uid}/items",
                    params={"page": 1, "per_page": 5}, tries=2,
                    headers={"Accept": "application/json", "Referer": BASE + "/"})
    if r is not None and r.status_code == 200:
        body = r.json()
        print(f"  top-level keys: {sorted(body.keys())}")
        u = body.get("user") or {}
        if u:
            print(f"      user counters: "
                  f"{ {k: v for k, v in u.items() if any(t in k for t in COUNTERISH)} }")

    print("\n=== 3. Next.js data endpoint, retried with escaped patterns ===")
    r = client._get("https://www.vinted.fr/", tries=1,
                    headers={"Accept": "text/html", "Sec-Fetch-Mode": "navigate"})
    build = None
    if r is not None and r.status_code == 200:
        for pat in (r'"buildId":"([^"]+)"', r'\\"buildId\\":\\"([^"\\]+)', r'/_next/static/([A-Za-z0-9_-]{8,})/'):
            m = re.search(pat, r.text)
            if m:
                build = m.group(1)
                print(f"  buildId candidate: {build}  (pattern {pat[:24]})")
                break
    if not build:
        print("  still no buildId — endpoint not reachable this way")
    else:
        for host, loc in ((BASE, "it"), ("https://www.vinted.fr", "fr")):
            url = f"{host}/_next/data/{build}/{loc}/items/9505849905.json"
            rr = client._get(url, tries=1, headers={"Accept": "application/json"})
            code = rr.status_code if rr is not None else "none"
            print(f"  {url[:70]}... -> HTTP {code}")
            if rr is not None and rr.status_code == 200 and "json" in rr.headers.get("content-type", ""):
                print(f"      has item_closing_action: {'item_closing_action' in rr.text}")

    print(f"\n[probe] traffic {client.bytes_uncompressed / 1024:.0f} KB")


if __name__ == "__main__":
    main()
