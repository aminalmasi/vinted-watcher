"""Which LIVE listings are attracting the most attention?

Vestiaire keeps offer counts private — they exist only in the seller's own
dashboard, and there is no public endpoint for them (`/products/{id}/offers`
and friends all 404). The nearest public signal is `likes`, the favourite
count, which the search API already returns on every hit. The people who
favourite an item are the pool that sends offers, so ranking by likes is the
closest we can get to "which shoes get the most offers".

Two honest caveats, both surfaced in the output rather than buried:

  * There is no sort-by-likes, so we cannot ASK for the most-liked items — we
    scan a sample and rank it ourselves.
  * A brand's live listings in the window run past the ~1000 pagination
    ceiling, so the sample is the NEWEST N per brand. That is why the headline
    ranking is likes-per-day, not raw likes: raw likes would just rediscover
    that older listings have had longer to collect them.

Runs on GitHub Actions — the search host 403s the university IP.
"""

from __future__ import annotations

import logging
import os
import random
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vestiaire.client import (FIELDS, FLOOR_CENTS, LOCALE, PAGE,  # noqa: E402
                              SEARCH, UA)
from vestiaire.run import BRANDS, SHOES_WOMEN                     # noqa: E402

WINDOW_DAYS = int(os.environ.get("VC_WINDOW_DAYS", "14"))
PAGES_PER_BRAND = int(os.environ.get("VC_PAGES", "8"))

log = logging.getLogger("hot")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9",
                  "Origin": "https://www.vestiairecollective.com",
                  "Referer": "https://www.vestiairecollective.com/",
                  "x-usecase": "catalog", "Content-Type": "application/json"})
STOP = False


def page(brand_id: str, offset: int, gte: int):
    global STOP
    if STOP:
        return [], 0
    time.sleep(random.uniform(6, 10))
    body = {"pagination": {"offset": offset, "limit": PAGE},
            "fields": FIELDS,
            "filters": {"brand.id": [brand_id], "categoryLvl0.id": [SHOES_WOMEN],
                        "sold": False, "price": {"gte": FLOOR_CENTS},
                        "createdAt": {"gte": gte}},
            "locale": LOCALE, "sort": "recency"}
    try:
        r = S.post(SEARCH, json=body, timeout=45)
    except requests.RequestException as exc:
        log.warning("%s", type(exc).__name__)
        return [], 0
    if r.status_code == 429:
        STOP = True
        log.warning("429 — stopping the scan")
        return [], 0
    if r.status_code != 200:
        log.warning("HTTP %d", r.status_code)
        return [], 0
    j = r.json()
    return (j.get("items") or []), int((j.get("paginationStats") or {}).get("totalHits") or 0)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    gte = int(time.time() - WINDOW_DAYS * 86400)
    now = time.time()
    rows, coverage = [], {}

    for bid, name in BRANDS.items():
        got, total = [], None
        for p in range(PAGES_PER_BRAND):
            items, hits = page(bid, p * PAGE, gte)
            if total is None:
                total = hits
            if not items:
                break
            got.extend(items)
        coverage[name] = (len(got), total or 0)
        log.info("%-20s sampled %4d of %5d live", name, len(got), total or 0)
        for it in got:
            likes = it.get("likes")
            created = it.get("createdAt")
            if likes is None or not created:
                continue
            age = max((now - created) / 86400, 0.25)
            rows.append({
                "brand": (it.get("brand") or {}).get("name") or name,
                "name": it.get("name") or "",
                "price": (it.get("price") or {}).get("cents", 0) / 100,
                "likes": likes, "age": age, "rate": likes / age,
                "url": "https://www.vestiairecollective.com" + (it.get("link") or ""),
            })

    if not rows:
        print("\nNo rows with a likes value — the field may have been renamed.")
        return 1

    liked = [r for r in rows if r["likes"] > 0]
    print(f"\nsampled {len(rows)} live listings, {len(liked)} with at least one like "
          f"({100*len(liked)/len(rows):.0f}%)")
    ls = sorted(r["likes"] for r in rows)
    print(f"likes: median {ls[len(ls)//2]}, p90 {ls[int(len(ls)*0.9)]}, max {ls[-1]}")

    print(f"\n=== hottest right now (likes per day on sale) ===")
    print(f"{'likes':>6} {'/day':>6} {'age':>5} {'price':>8}  brand / item")
    for r in sorted(rows, key=lambda r: -r["rate"])[:25]:
        print(f"{r['likes']:>6} {r['rate']:>6.1f} {r['age']:>4.0f}d {r['price']:>8.0f}  "
              f"{r['brand']} — {r['name'][:44]}")
        print(f"       {r['url']}")

    print(f"\n=== most liked overall in the sample ===")
    for r in sorted(rows, key=lambda r: -r["likes"])[:10]:
        print(f"{r['likes']:>6} ❤  {r['age']:>4.0f}d {r['price']:>8.0f}  "
              f"{r['brand']} — {r['name'][:44]}")

    thin = [n for n, (got, tot) in coverage.items() if tot and got < tot]
    if thin:
        print(f"\nNOTE: sample is the newest {PAGES_PER_BRAND*PAGE} per brand; "
              f"not exhaustive for {', '.join(thin)}.")
    print("\ndone" + (" (cut short by a 429)" if STOP else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
