"""How wide is the window really, and did we just retire a live recent listing?

Two things to establish before claiming "we know about anything posted in the
last two weeks":
  1. The id range the sweep actually covers, and how fast new ids arrive — that
     gives the real age of the oldest listing we can still see.
  2. Whether 9515145305, retired today as "aged out", is in fact recent and live.
     If so, "absent from 3 sweeps" is catching paging misses, not ageing, and
     retiring on it loses a live listing we should still be watching.
"""

import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vintedwatch.client import BASE, VintedClient  # noqa: E402

SUSPECT = 9515145305


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(message)s")
    state = json.load(open("data/state.json"))
    client = VintedClient(token_cache=state.get("token"))

    print("=== 1. id range across the whole window ===")
    ids, pages_seen = [], 0
    for page in (1, 5, 10):
        items = client.search({"search_text": "prada shoes", "order": "newest_first",
                               "per_page": 96}, page=page)
        if not items:
            print(f"  page {page}: no items")
            continue
        pages_seen += 1
        page_ids = [i["id"] for i in items]
        ids += page_ids
        print(f"  page {page:>2}: ids {min(page_ids)} .. {max(page_ids)}")
    if ids:
        span = max(ids) - min(ids)
        print(f"  newest id {max(ids)}, oldest id {min(ids)}, span {span:,}")

    print("\n=== 2. is the retired listing still live? ===")
    r = client._get(BASE + "/api/v2/catalog/items",
                    params={"search_text": "Prada collapse sneakers nylon",
                            "per_page": 96, "page": 1},
                    tries=2, headers={"Accept": "application/json",
                                      "Referer": BASE + "/catalog"})
    found = None
    if r is not None and r.status_code == 200:
        found = next((i for i in r.json().get("items", []) if i.get("id") == SUSPECT), None)
    if found:
        sid = (found.get("user") or {}).get("id")
        print(f"  {SUSPECT}: STILL LIVE (seller {sid})")
        print(f"      wardrobe still_listed={client.still_listed(SUSPECT, sid)}")
        print("      -> retiring it lost a live, RECENT listing we should still watch")
    else:
        print(f"  {SUSPECT}: not found by title search")

    print("\n=== 3. how many of the newest 96 are ones we already track? ===")
    tracked = set(int(k) for k in state["items"])
    page1 = client.search({"search_text": "prada shoes", "order": "newest_first",
                           "per_page": 96}, page=1) or []
    known = sum(1 for i in page1 if i["id"] in tracked)
    print(f"  {known}/{len(page1)} of page 1 already tracked "
          f"({len(page1) - known} would be new this sweep)")

    print(f"\n[probe] traffic {client.bytes_uncompressed / 1024:.0f} KB")


if __name__ == "__main__":
    main()
