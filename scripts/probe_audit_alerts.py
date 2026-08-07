"""What fraction of the alerts we SENT point at a listing that is still live?

The counter test proves the seller parted with something, not that this
particular listing was the thing. If our item was merely hidden while the seller
happened to sell a different one, we would have sent a bad link.

Method: search the catalog for each alerted listing's own title. If its id comes
back, the listing is still on sale and the alert was wrong. Finding it is proof;
not finding it is only consistent with being gone, so this measures a LOWER
BOUND on the false-positive rate.
"""

import json
import logging
import os
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vintedwatch.client import BASE, VintedClient  # noqa: E402

SAMPLE = 30


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(message)s")
    state = json.load(open("data/state.json"))
    client = VintedClient(token_cache=state.get("token"))
    now = time.time()

    recent = [(k, v) for k, v in state["sold"].items()
              if v.get("reported_at", 0) > now - 24 * 3600]
    recent.sort(key=lambda kv: -kv[1].get("reported_at", 0))
    pool = recent[:SAMPLE]
    print(f"auditing {len(pool)} of {len(recent)} alerts sent in the last 24 h\n")

    live = gone = unclear = 0
    for key, v in pool:
        title = (v.get("title") or "").strip()
        if not title or len(title) < 4:
            unclear += 1
            print(f"  {key}: title too generic to search ({title!r})")
            continue
        r = client._get(BASE + "/api/v2/catalog/items",
                        params={"search_text": title[:60], "order": "newest_first",
                                "per_page": 24, "page": 1},
                        tries=2, headers={"Accept": "application/json",
                                          "Referer": BASE + "/catalog"})
        if r is None or r.status_code != 200:
            unclear += 1
            print(f"  {key}: search failed")
            continue
        try:
            items = r.json().get("items", [])
        except ValueError:
            unclear += 1
            continue
        hit = any(i.get("id") == int(key) for i in items)
        if hit:
            live += 1
            print(f"  {key}: ❌ STILL LIVE — bad link | delta={v.get('given_delta')} "
                  f"| {title[:44]}")
        else:
            gone += 1
            print(f"  {key}: ✅ gone (delta={v.get('given_delta')}) | {title[:44]}")
        time.sleep(random.uniform(1.5, 3.0))

    checked = live + gone
    print(f"\n=== RESULT over {checked} checkable alerts ===")
    if checked:
        print(f"  still live (definitely wrong): {live}  ({100*live/checked:.0f}%)")
        print(f"  gone (consistent with sold):   {gone}  ({100*gone/checked:.0f}%)")
    print(f"  unclear/not searchable:        {unclear}")
    print(f"\n[probe] traffic {client.bytes_uncompressed/1024/1024:.1f} MB uncompressed")


if __name__ == "__main__":
    main()
