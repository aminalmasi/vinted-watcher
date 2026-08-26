"""Vocabularies for matching a Vinted listing against Vestiaire.

Matching on model NAMES is unreliable in both directions — half of Vinted's
titles are just "Sandals", and Vestiaire's model field is often empty or wrong.
So the matcher has to lean on structured attributes instead, which means we
need their id vocabularies: condition, colour, material, size, subcategory.

Facets give all of that in a handful of requests, with counts, so we also learn
which values are actually populated rather than merely defined.

Also settles one thing the design depends on: what does OMITTING the `sold`
filter return — live only, or live plus sold? "Similar across everything" needs
the answer, and guessing it wrong would silently halve the comparison set.
"""

from __future__ import annotations

import json, logging, os, random, sys, time
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vestiaire.client import FIELDS, LOCALE, SEARCH, UA   # noqa: E402
from vestiaire.run import SHOES_WOMEN                     # noqa: E402

log = logging.getLogger("vocab")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9",
                  "Origin": "https://www.vestiairecollective.com",
                  "Referer": "https://www.vestiairecollective.com/",
                  "x-usecase": "catalog", "Content-Type": "application/json"})


def q(filters, facets=None, limit=1):
    time.sleep(random.uniform(6, 10))
    b = {"pagination": {"offset": 0, "limit": limit}, "fields": FIELDS,
         "filters": filters, "locale": LOCALE, "sort": "recency"}
    if facets:
        b["facets"] = {"fields": facets}
    r = S.post(SEARCH, json=b, timeout=45)
    if r.status_code != 200:
        log.warning("HTTP %d", r.status_code)
        return None
    return r.json()


def hits(j):
    return (j or {}).get("paginationStats", {}).get("totalHits")


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    shoes = {"categoryLvl0.id": [SHOES_WOMEN]}

    print("=== does omitting `sold` include sold items? ===")
    live = hits(q(shoes | {"sold": False}))
    sold = hits(q(shoes | {"sold": True}))
    both = hits(q(shoes))
    print(f"  sold=False {live}\n  sold=True  {sold}\n  omitted    {both}")
    print("  -> omitting looks like " +
          ("LIVE ONLY" if both == live else
           "LIVE+SOLD" if both and live and both > live else "unclear (10k cap?)"))

    print("\n=== vocabularies (women's shoes) ===")
    j = q(shoes, facets=["condition", "color", "materialLvl0", "categoryLvl1",
                         "size0", "sellerBadge"], limit=1)
    fields = ((j or {}).get("facets") or {}).get("fields") or {}
    for name, vals in fields.items():
        print(f"\n-- {name} ({len(vals)} values)")
        for v in vals[:22]:
            print(f"   id={str(v.get('id')):>6}  {str(v.get('name'))[:38]:<38} "
                  f"{v.get('count')}")

    print("\n=== do attribute filters actually narrow? ===")
    base = hits(q(shoes | {"sold": True}))
    for label, extra in [("condition", {"condition": [fields.get("condition", [{}])[0].get("id")]}),
                         ("color", {"color": [fields.get("color", [{}])[0].get("id")]})]:
        if not list(extra.values())[0][0]:
            continue
        n = hits(q(shoes | {"sold": True} | extra))
        print(f"  + {label:10} {base} -> {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
