"""How far back do Vestiaire's sold listings reach, and can we harvest them?

Our own collection started on 2026-08-16, so we hold ~10 days of sales. But the
sold FILTER is not limited to what we happened to watch — if their index keeps
old sales and `createdAt` can be sliced into historical windows, then a price
history (and an image corpus) can be backfilled immediately rather than waiting
weeks to accumulate.

Three things decide that:
  1. how many sold listings exist per year, per brand;
  2. whether sold hits still carry `pictures` (no photos, no image index);
  3. whether a narrow window stays under the ~1000 pagination ceiling, since
     anything above it can be counted but not enumerated.
"""

from __future__ import annotations

import calendar, json, logging, os, random, sys, time
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vestiaire.client import FIELDS, LOCALE, PAGE, SEARCH, UA   # noqa: E402
from vestiaire.run import SHOES_WOMEN                           # noqa: E402

log = logging.getLogger("hist")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9",
                  "Origin": "https://www.vestiairecollective.com",
                  "Referer": "https://www.vestiairecollective.com/",
                  "x-usecase": "catalog", "Content-Type": "application/json"})


def epoch(y, m=1, d=1):
    return int(calendar.timegm((y, m, d, 0, 0, 0, 0, 0, 0)))


def q(brand, gte, lte, limit=3):
    time.sleep(random.uniform(6, 10))
    f = {"brand.id": [brand], "categoryLvl0.id": [SHOES_WOMEN], "sold": True,
         "createdAt": {"gte": gte, "lte": lte}}
    r = S.post(SEARCH, json={"pagination": {"offset": 0, "limit": limit},
                             "fields": FIELDS, "filters": f, "locale": LOCALE,
                             "sort": "recency"}, timeout=45)
    if r.status_code != 200:
        log.warning("HTTP %d", r.status_code)
        return None
    return r.json()


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    BR = {"60": "Prada", "236": "Christian Louboutin"}

    print("sold women's shoes by LISTING year (totalHits; 10000 = capped)\n")
    print(f"{'year':>6} " + " ".join(f"{n:>20}" for n in BR.values()))
    for y in range(2016, 2027):
        row = []
        for bid in BR:
            j = q(bid, epoch(y), epoch(y + 1))
            row.append((j or {}).get("paginationStats", {}).get("totalHits"))
        print(f"{y:>6} " + " ".join(f"{str(v):>20}" for v in row))

    print("\ndo old sold hits still carry photos?")
    for y in (2018, 2022, 2025):
        j = q("60", epoch(y), epoch(y + 1), limit=3)
        items = (j or {}).get("items") or []
        withpic = sum(1 for i in items if i.get("pictures"))
        ex = (items[0].get("pictures") or ["-"])[0] if items else "-"
        print(f"  {y}: {len(items)} sampled, {withpic} with pictures  e.g. {str(ex)[:60]}")

    print("\nhow narrow must a window be to stay enumerable (<1000)?")
    for label, gte, lte in [("2025 full year", epoch(2025), epoch(2026)),
                            ("2025 Q1", epoch(2025, 1), epoch(2025, 4)),
                            ("2025 January", epoch(2025, 1), epoch(2025, 2))]:
        j = q("60", gte, lte)
        n = (j or {}).get("paginationStats", {}).get("totalHits")
        print(f"  Prada, {label:16} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
