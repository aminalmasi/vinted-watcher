"""Run the real confirmation logic over the listings that actually vanished.

This is the production population: listings the watcher has seen leave the feed.
If the 'sold' branch works at all, some of these should trigger it. Also tries
the wardrobe endpoint, which is where a seller's sold items are reachable.
"""

import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vintedwatch.client import BASE, VintedClient  # noqa: E402

MAX_CHECKS = 12


def try_wardrobe(client, uid):
    """A seller's closet, where sold items remain visible."""
    for path in (f"/api/v2/wardrobe/{uid}/items", f"/api/v2/users/{uid}/items"):
        r = client._get(BASE + path, tries=1,
                        headers={"Accept": "application/json", "Referer": BASE + "/"})
        if r is None:
            print(f"  {path} -> no response")
            continue
        print(f"  {path} -> HTTP {r.status_code}")
        if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
            items = r.json().get("items", [])
            print(f"      {len(items)} items; keys: {sorted(items[0].keys())[:14] if items else '-'}")
            return items
    return []


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    state = json.load(open("data/state.json"))
    items = state["items"]

    absent = [v for v in items.values() if v.get("missing_runs", 0) >= 1]
    absent.sort(key=lambda r: -r.get("missing_runs", 0))
    print(f"state: {len(items)} tracked, {len(absent)} absent from the last poll, "
          f"{len(state['sold'])} recorded sold\n")

    client = VintedClient(token_cache=state.get("token"))
    client.bootstrap()

    print(f"=== running the real check_sold() over {min(len(absent), MAX_CHECKS)} vanished listings ===")
    verdicts = {}
    for rec in absent[:MAX_CHECKS]:
        v = client.check_sold(rec["id"], rec.get("url"))
        verdicts[v] = verdicts.get(v, 0) + 1
        print(f"  {v:8s} missing_runs={rec.get('missing_runs')}  {rec.get('title', '')[:46]}")
        print(f"           {rec.get('url')}")
        time.sleep(3)
    print(f"\n  verdict tally: {verdicts}")

    print("\n=== wardrobe endpoint (where sold items stay visible) ===")
    feed = client.search({"search_text": "prada shoes", "order": "newest_first", "per_page": 24})
    if feed:
        wardrobe = try_wardrobe(client, feed[0]["user"]["id"])
        for it in wardrobe[:12]:
            flags = {k: it[k] for k in it
                     if any(t in k for t in ("closed", "sold", "hidden", "reserv"))}
            if flags:
                print(f"      item {it.get('id')} flags={flags}")

    print(f"\n[probe] traffic {client.bytes_uncompressed / 1024 / 1024:.1f} MB uncompressed")


if __name__ == "__main__":
    main()
