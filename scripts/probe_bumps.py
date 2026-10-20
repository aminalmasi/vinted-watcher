"""Are old (bumped) listings mixed into newest-first feeds?

Vinted lets sellers pay to push an old listing back to the top. If such
listings sit among fresh ones, any rule that reads a page's LOW ids to decide
how far back the feed reaches will stop far too early. Print each page's ids
and flag the ones much older than their page's median.
"""
import sys, time
sys.path.insert(0, "scripts")
from vinted_steals import UA, cards
import requests

IDS_PER_HOUR = 294_000          # measured from the frontiers over the first day
s = requests.Session()
s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
s.get("https://www.vinted.it/", timeout=45)
for label, params in (("Gucci, no filter", [("brand_ids[]", 567)]),
                      ("Dior, price 100+", [("currency", "EUR"), ("price_from", 100), ("brand_ids[]", 671),
                                            ("brand_ids[]", 15430438), ("brand_ids[]", 168598)])):
    print(f"\n{label}")
    for p in (1, 2, 3):
        time.sleep(5)
        cs = cards(s.get("https://www.vinted.it/catalog", params=params + [("order", "newest_first"), ("page", p)],
                         timeout=60).text)
        org = [c["id"] for c in cs if not c["promoted"]]
        med = sorted(org)[len(org) // 2]
        old = [(i, round((med - i) / IDS_PER_HOUR, 1)) for i in org if med - i > 6 * IDS_PER_HOUR]
        desc = sorted(org, reverse=True)
        print(f"  page {p}: {len(cs)} cards ({len(cs)-len(org)} promoted), {len(old)} organic listings >6h older "
              f"than the page median, e.g. {[h for _, h in old[:6]]} h older; page spans "
              f"{(desc[0]-desc[int(len(desc)*0.75)])/IDS_PER_HOUR:.1f}h at 75%, "
              f"{(desc[0]-desc[int(len(desc)*0.9)])/IDS_PER_HOUR:.1f}h at 90%, {(desc[0]-desc[-1])/IDS_PER_HOUR:.1f}h at the bottom")
