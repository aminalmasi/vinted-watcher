"""How many women's shoes exist on Vestiaire beyond our ten brands?

Distractors should be sampled broadly, not by visual similarity: an index
salted with look-alikes is an index full of unlabelled near-positives, which
get scored as retrieval errors and depress mAP for reasons unrelated to the
encoder.

So the question is simply how much same-domain, same-category stock exists
outside the ten tracked brands. Facet counts answer it in one request and are
not subject to the 10,000 totalHits cap, though they ARE approximate (an
Elasticsearch terms aggregation, which undercounted our brands by ~2.8x when
checked against a full walk) - so treat the answer as a floor.
"""
import json, os, sys, time, random
import requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vestiaire.client import FIELDS, LOCALE, SEARCH, UA
from vestiaire.run import BRANDS, SHOES_WOMEN

S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9",
                  "Origin": "https://www.vestiairecollective.com",
                  "Referer": "https://www.vestiairecollective.com/",
                  "x-usecase": "catalog", "Content-Type": "application/json"})

def q(sold):
    time.sleep(random.uniform(6, 10))
    r = S.post(SEARCH, json={"pagination": {"offset": 0, "limit": 1}, "fields": FIELDS,
        "filters": {"categoryLvl0.id": [SHOES_WOMEN], "sold": sold},
        "facets": {"fields": ["brand"]}, "locale": LOCALE, "sort": "recency"}, timeout=45)
    return r.json() if r.status_code == 200 else {}

for sold, label in ((False, "LIVE"), (True, "SOLD")):
    j = q(sold)
    br = ((j.get("facets") or {}).get("fields") or {}).get("brand") or []
    ours = sum(b["count"] for b in br if str(b["id"]) in BRANDS)
    others = sum(b["count"] for b in br if str(b["id"]) not in BRANDS)
    print(f"\n{label} women's shoes — facet over {len(br)} brands returned")
    print(f"  our 10 brands   {ours:>10,}")
    print(f"  other brands    {others:>10,}   <- candidate distractors")
    print(f"  (facets undercount ~2.8x, so the real figure is far higher)")
    print("  biggest non-tracked brands:")
    for b in [x for x in br if str(x["id"]) not in BRANDS][:10]:
        print(f"     {b['name'][:28]:<30}{b['count']:>9,}")
