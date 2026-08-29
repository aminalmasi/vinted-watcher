"""How many LIVE listings are there, and what would 5 photos each cost?

Counting this naively is impossible: totalHits saturates at 10,000, so a plain
per-brand query just returns 10000 ten times. Facets are not capped — during
the brand ranking they returned per-brand counts summing far beyond 10k — so a
single faceted query gives exact live counts for every brand at once.
"""

from __future__ import annotations

import json, logging, os, random, sys, time
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vestiaire.client import FIELDS, LOCALE, SEARCH, UA   # noqa: E402
from vestiaire.run import BRANDS, SHOES_WOMEN             # noqa: E402

log = logging.getLogger("vol")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9",
                  "Origin": "https://www.vestiairecollective.com",
                  "Referer": "https://www.vestiairecollective.com/",
                  "x-usecase": "catalog", "Content-Type": "application/json"})


def q(filters, facets):
    time.sleep(random.uniform(6, 10))
    r = S.post(SEARCH, json={"pagination": {"offset": 0, "limit": 1},
                             "fields": FIELDS, "filters": filters,
                             "facets": {"fields": facets},
                             "locale": LOCALE, "sort": "recency"}, timeout=45)
    if r.status_code != 200:
        log.warning("HTTP %d", r.status_code)
        return {}
    return r.json()


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    base = {"categoryLvl0.id": [SHOES_WOMEN], "sold": False}
    j = q(base, ["brand", "condition"])
    fields = (j.get("facets") or {}).get("fields") or {}
    brands = {str(b["id"]): b for b in fields.get("brand", [])}

    print(f"LIVE women's shoes  (total in scope: "
          f"{(j.get('paginationStats') or {}).get('totalHits')} — capped)\n")
    print(f"{'brand':<24}{'live':>10}")
    tot = 0
    for bid, name in BRANDS.items():
        n = brands.get(bid, {}).get("count", 0)
        tot += n
        print(f"{name:<24}{n:>10,}")
    print(f"{'TOTAL':<24}{tot:>10,}")

    print("\ncondition mix across all live shoes:")
    for c in fields.get("condition", []):
        print(f"   {c.get('name','?'):<32}{c.get('count',0):>10,}")

    imgs = tot * 5
    print(f"\n=== cost of 5 photos each ===")
    print(f"  listings              {tot:>12,}")
    print(f"  images                {imgs:>12,}")
    print(f"  apiv2 calls (cluster) {tot:>12,}   for the photo lists")
    for gap, label in ((5.2, "measured-safe 4-6 s"),):
        rh = imgs * gap / 3600
        print(f"  runner-hours @ {label}: {rh:,.0f}")
        for c in (10, 20):
            print(f"     at {c:>2} concurrent: {rh/c:>6.1f} h  ({rh/c/24:.1f} days)")
    print(f"  storage @ 21.6 KB     {imgs*21.6/1024/1024:>12,.1f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
