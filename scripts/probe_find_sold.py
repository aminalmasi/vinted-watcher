"""Find genuinely sold listings and check that check_sold() calls them sold.

Seller wardrobes (`/api/v2/wardrobe/{uid}/items`) list a seller's items with
`is_closed` / `is_reserved` / `is_hidden` flags, sold ones included. That gives
ground truth to test the item-page logic against.

It is also a much cheaper signal than the 2.4 MB item page, so this measures
whether the watcher should be using it instead.
"""

import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vintedwatch.client import BASE, VintedClient  # noqa: E402

SELLERS_TO_SCAN = 12
CONFIRMATIONS = 4


def wardrobe(client, uid, page=1):
    r = client._get(f"{BASE}/api/v2/wardrobe/{uid}/items",
                    params={"page": page, "per_page": 40}, tries=2,
                    headers={"Accept": "application/json", "Referer": BASE + "/"})
    if r is None or r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
        return []
    return r.json().get("items", [])


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(message)s")
    client = VintedClient()
    client.bootstrap()

    feed = client.search({"search_text": "prada shoes", "order": "newest_first", "per_page": 24})
    feed += client.search({"search_text": "prada", "order": "newest_first", "per_page": 24}) or []
    sellers, seen = [], set()
    for it in feed or []:
        uid = it["user"]["id"]
        if uid not in seen:
            seen.add(uid)
            sellers.append(uid)

    print(f"scanning {min(len(sellers), SELLERS_TO_SCAN)} seller wardrobes for closed listings\n")
    closed, total = [], 0
    for uid in sellers[:SELLERS_TO_SCAN]:
        items = wardrobe(client, uid)
        total += len(items)
        hits = [i for i in items if i.get("is_closed") or i.get("is_hidden")]
        if hits:
            print(f"  seller {uid}: {len(hits)}/{len(items)} closed-or-hidden")
            for h in hits:
                print(f"      id={h['id']} is_closed={h.get('is_closed')} "
                      f"is_hidden={h.get('is_hidden')} is_reserved={h.get('is_reserved')} "
                      f"| {str(h.get('title'))[:40]}")
            closed.extend(hits)
        time.sleep(1)

    print(f"\nscanned {total} listings, found {len(closed)} closed/hidden")
    if not closed:
        print("no sold listings available to test against right now")
        return

    print(f"\n=== does check_sold() agree on {min(len(closed), CONFIRMATIONS)} of them? ===")
    for h in closed[:CONFIRMATIONS]:
        url = h.get("url") or f"{BASE}/items/{h['id']}"
        verdict = client.check_sold(h["id"], url)
        expected = "sold" if h.get("is_closed") else "removed"
        mark = "OK " if verdict == expected else "MISMATCH"
        print(f"  [{mark}] item {h['id']}: wardrobe says is_closed={h.get('is_closed')} "
              f"-> check_sold()={verdict} (expected {expected})")
        print(f"           {url}")
        time.sleep(3)

    print(f"\n[probe] traffic {client.bytes_uncompressed / 1024 / 1024:.1f} MB uncompressed")


if __name__ == "__main__":
    main()
