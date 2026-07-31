"""What would five brands actually cost?

Each search gets its own 960-item cap, so five brands means five sweeps. Before
promising "no misses, no blocks" we need the real numbers: how deep each brand's
result set is, and how many bytes and requests a full cycle would take.
"""

import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vintedwatch.client import BASE, VintedClient  # noqa: E402

BRANDS = [
    "prada shoes",
    "miu miu shoes",
    "maison margiela shoes",
    "christian louboutin shoes",
    "ferragamo shoes",
]


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(message)s")
    state = json.load(open("data/state.json"))
    client = VintedClient(token_cache=state.get("token"))

    print(f"{'query':30s} {'total':>7} {'pages':>6} {'KB/page':>8} {'sweep MB':>9}")
    print("-" * 64)
    grand_pages = grand_bytes = 0
    for q in BRANDS:
        before = client.bytes_uncompressed
        items = client.search({"search_text": q, "order": "newest_first",
                               "per_page": 96}, page=1)
        if items is None:
            print(f"{q:30s}  FAILED")
            continue
        pag = client.last_pagination or {}
        total = pag.get("total_entries") or 0
        pages = pag.get("total_pages") or 0
        kb = (client.bytes_uncompressed - before) / 1024
        sweep_mb = kb * pages / 1024
        grand_pages += pages
        grand_bytes += kb * pages
        print(f"{q:30s} {total:>7} {pages:>6} {kb:>8.0f} {sweep_mb:>9.1f}")
        time.sleep(2)

    unc_mb = grand_bytes / 1024
    met_mb = unc_mb / 7  # gzip ratio measured on this API
    print("-" * 64)
    print(f"one full cycle: {grand_pages} requests, {unc_mb:.1f} MB uncompressed, "
          f"~{met_mb:.1f} MB metered")
    for hours, label in ((1, "hourly"), (2, "every 2h"), (3, "every 3h"), (6, "every 6h")):
        per_day = 24 / hours
        print(f"  {label:10s} -> {grand_pages * per_day:5.0f} req/day, "
              f"{met_mb * per_day * 30 / 1024:5.2f} GB/month")

    print(f"\ncurrent state.json: {os.path.getsize('data/state.json')/1024:.0f} KB "
          f"for {len(state['items'])} listings")
    per = os.path.getsize('data/state.json') / max(len(state['items']), 1)
    print(f"  ~{per:.0f} bytes/listing -> 5 brands would be roughly "
          f"{per * 4800 / 1024 / 1024:.1f} MB, committed to git every run")


if __name__ == "__main__":
    main()
