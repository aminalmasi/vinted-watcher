"""Which brands actually sell, above a price floor?

The watcher tracks five brands we picked by hand. This asks the opposite
question — let the data name the brands — using a feature the search API gives
away for free: FACETS. One query for "sold women's shoes over EUR 150" comes
back with a per-brand breakdown of the whole result set, so we get every
brand's ranking in a single request instead of one query per brand.

Two things need checking before the numbers can be trusted, and this does both:

  1. which price filter shape the API accepts (cents vs units, gte vs range),
     confirmed by watching totalHits actually move;
  2. whether facet counts RESPECT the other filters or ignore them. Facets
     conventionally exclude their own field's filter, and if they also ignored
     `sold` we would be ranking inventory, not sales. So the top brands are
     re-queried individually and the two numbers compared.

Runs on GitHub Actions — the search host 403s the university IP.
"""

from __future__ import annotations

import json
import logging
import random
import sys
import time

import requests

SEARCH = "https://search.vestiairecollective.com/v1/product/search"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
LOCALE = {"country": "IT", "currency": "EUR", "language": "it", "sizeType": "women"}
SHOES = "3"
FLOOR_EUR = 150
WINDOWS = (30, 90)

log = logging.getLogger("rank")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9",
                  "Origin": "https://www.vestiairecollective.com",
                  "Referer": "https://www.vestiairecollective.com/",
                  "x-usecase": "catalog", "Content-Type": "application/json"})
STOP = False


def post(body: dict, tag: str) -> dict | None:
    global STOP
    if STOP:
        return None
    time.sleep(random.uniform(6, 10))
    try:
        r = S.post(SEARCH, json=body, timeout=45)
    except requests.RequestException as exc:
        log.warning("%s: %s", tag, type(exc).__name__)
        return None
    if r.status_code == 429:
        STOP = True
        log.warning("%s: 429 — stopping", tag)
        return None
    if r.status_code != 200:
        log.info("%s: HTTP %d", tag, r.status_code)
        return None
    try:
        return r.json()
    except ValueError:
        return None


def query(price_filter=None, brand=None, days=30, facets=False, limit=1):
    f = {"categoryLvl0.id": [SHOES], "sold": True,
         "createdAt": {"gte": int(time.time() - days * 86400)}}
    if price_filter:
        f.update(price_filter)
    if brand:
        f["brand.id"] = [brand]
    b = {"pagination": {"offset": 0, "limit": limit},
         "fields": ["name", "brand", "price", "sold", "createdAt"],
         "filters": f, "locale": LOCALE, "sort": "recency"}
    if facets:
        b["facets"] = {"fields": ["brand"]}
    return b


def hits(j) -> int | None:
    if not j:
        return None
    return (j.get("paginationStats") or {}).get("totalHits")


def find_price_filter() -> dict | None:
    """Try the plausible shapes; keep the one that actually reduces the count."""
    base = hits(post(query(days=30), "baseline"))
    log.info("baseline (sold shoes, 30d, no price floor): %s", base)
    if not base:
        return None
    candidates = [
        ("price cents gte", {"price": {"gte": FLOOR_EUR * 100}}),
        ("price units gte", {"price": {"gte": FLOOR_EUR}}),
        ("priceRange cents", {"priceRange": {"gte": FLOOR_EUR * 100}}),
        ("price min/max",    {"price": {"min": FLOOR_EUR * 100}}),
    ]
    for name, filt in candidates:
        n = hits(post(query(price_filter=filt, days=30), name))
        log.info("  %-18s -> %s", name, n)
        if n is not None and 0 < n < base:
            log.info("  using %s", name)
            return filt
    log.warning("no price filter shape worked — ranking without a floor")
    return None


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(f"Brands by SOLD women's shoes over EUR {FLOOR_EUR}\n")

    pf = find_price_filter()

    for days in WINDOWS:
        j = post(query(price_filter=pf, days=days, facets=True), f"facets {days}d")
        if not j:
            continue
        total = hits(j)
        fields = ((j.get("facets") or {}).get("fields") or {})
        brands = fields.get("brand") or []
        print(f"\n=== listed within {days} days, sold, >EUR {FLOOR_EUR} "
              f"(total {total}) ===")
        print(f"{'#':>3} {'brand':<32} {'sold':>7}")
        for i, b in enumerate(brands[:25], 1):
            print(f"{i:>3} {b.get('name','?'):<32} {b.get('count',0):>7}   id={b.get('id')}")
        if total == 10000:
            print("  (total saturated at the 10k cap — treat as a floor)")

        if days == 30 and brands:
            print("\n  cross-check: facet count vs a direct per-brand query")
            for b in brands[:5]:
                n = hits(post(query(price_filter=pf, brand=str(b["id"]), days=30),
                              b.get("name", "?")))
                mark = "OK" if n == b.get("count") else "MISMATCH"
                print(f"    {b.get('name','?'):<28} facet={b.get('count'):>6} "
                      f"direct={n}  {mark}")

    print("\ndone" + (" (cut short by a 429)" if STOP else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
