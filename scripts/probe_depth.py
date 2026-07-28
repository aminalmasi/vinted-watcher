"""How deep is the 'prada shoes' result set, and what would a full sweep cost?

Decides whether tracking the whole search (so absence really means gone) is
affordable, or whether we must keep guessing from a 190-item window.

Measures: total results, per-page metered bytes, and whether larger pages are
reliable over slow residential exits (per_page=96 previously timed out).
"""

import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vintedwatch.client import BASE, VintedClient  # noqa: E402


def page(client, per_page, page_no):
    before = client.bytes_uncompressed
    t0 = time.time()
    r = client._get(BASE + "/api/v2/catalog/items",
                    params={"search_text": "prada shoes", "order": "newest_first",
                            "per_page": per_page, "page": page_no},
                    tries=2, headers={"Accept": "application/json",
                                      "Referer": BASE + "/catalog"})
    dt = time.time() - t0
    if r is None or r.status_code != 200:
        return None, dt, 0
    body = r.json()
    return body, dt, client.bytes_uncompressed - before


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(message)s")
    state = json.load(open("data/state.json"))
    client = VintedClient(token_cache=state.get("token"))

    print("=== 1. how big is the result set? ===")
    body, dt, size = page(client, 24, 1)
    if not body:
        sys.exit("page 1 failed")
    pag = body.get("pagination") or {}
    print(f"  pagination: {json.dumps(pag)}")
    total = pag.get("total_entries")
    print(f"  total_entries={total}  total_pages(@24)={pag.get('total_pages')}")
    print(f"  page 1: {dt:.1f}s, {size/1024:.0f} KB uncompressed")

    print("\n=== 2. is a deep page even reachable? ===")
    for p in (20, 40, 80):
        body, dt, size = page(client, 24, p)
        n = len(body.get("items", [])) if body else 0
        print(f"  page {p:>3}: {'OK ' if body else 'FAIL'} {n:>2} items, {dt:.1f}s, {size/1024:.0f} KB")
        time.sleep(3)

    print("\n=== 3. do bigger pages work on residential exits? ===")
    for pp in (48, 96):
        body, dt, size = page(client, pp, 1)
        n = len(body.get("items", [])) if body else 0
        print(f"  per_page={pp:>3}: {'OK ' if body else 'FAIL'} {n:>3} items, "
              f"{dt:.1f}s, {size/1024:.0f} KB  ({size/1024/max(n,1):.1f} KB per listing)")
        time.sleep(3)

    print(f"\n[probe] total {client.bytes_uncompressed/1024:.0f} KB uncompressed "
          f"(~{client.bytes_uncompressed/1024/7:.0f} KB metered)")


if __name__ == "__main__":
    main()
