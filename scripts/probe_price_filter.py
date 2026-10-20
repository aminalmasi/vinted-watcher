"""Does Vinted's price filter return every listing in the band?

The first price-band sweep saw far fewer recent listings than were known to
exist: Gucci EUR 0-35 page 1 reached days back while 142 listings in that
band were uploaded in the last 12h, 92 of them not shown. The tracker's old
probe only showed that returned listings were IN band, never that in-band
listings were all returned. For missed listings, try each way of writing the
filter, and the plain brand feed, and see which ones find them.
"""
import json, re, sys, time
sys.path.insert(0, "scripts")
from vinted_steals import UA, cards
import requests

s = requests.Session()
s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
s.get("https://www.vinted.it/", timeout=45)
st = json.load(open("data/steals/state.json"))
brands = json.load(open("data/steals/brands.json"))


def look(ids, extra, targets, pages):
    found, total = {}, None
    params = extra + [("brand_ids[]", b) for b in ids] + [("order", "newest_first")]
    for p in range(1, pages + 1):
        time.sleep(5)
        r = s.get("https://www.vinted.it/catalog", params=params + [("page", p)], timeout=60)
        if total is None:
            m = re.search(r'\\?"total_entries\\?":(\d+)', r.text)
            total = int(m.group(1)) if m else None
        cs = cards(r.text)
        for c in cs:
            if c["id"] in targets and c["id"] not in found:
                found[c["id"]] = (p, c["price"])
        org = [c["id"] for c in cs if not c["promoted"]]
        if not org or min(org) < min(targets):
            break
    return found, total


for group, lo, hi in (("Gucci", None, 35), ("Dior", 100, None)):
    miss = [r for r in st["watch"].values() if r["group"] == group and r.get("misses", 0) >= 1
            and lo is None or (r["group"] == group and r.get("misses", 0) >= 1 and float(r.get("price") or 0) >= (lo or 0))]
    miss = [r for r in miss if r["group"] == group and (hi is None or float(r.get("price") or 0) < hi)
            and (lo is None or float(r.get("price") or 0) >= lo)][:12]
    targets = {int(r["id"]) for r in miss}
    print(f"\n{group} band {lo}-{hi}: {len(targets)} missed listings, recorded prices "
          f"{sorted(float(r['price']) for r in miss)}")
    variants = {
        "brand feed, no price filter": [],
        "currency=EUR + one-sided": [("currency", "EUR")] + ([("price_from", lo)] if lo else []) + ([("price_to", hi)] if hi else []),
        "no currency, one-sided": ([("price_from", lo)] if lo else []) + ([("price_to", hi)] if hi else []),
        "currency=EUR + both sides": [("currency", "EUR"), ("price_from", lo or 0), ("price_to", hi or 100000)],
    }
    for name, extra in variants.items():
        found, total = look(brands[group], extra, targets, 10)
        print(f"  {name:<30} total {total}, found {len(found)} of {len(targets)} "
              f"{sorted(found.values())[:6]}", flush=True)
