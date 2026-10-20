"""Does a single-category Vinted search return listings from other categories?

In the multi-category probe, Gucci women's gloves (373) and men's gloves (186)
shared listings on their first pages, though a listing sits in exactly one
category, and Bottega Veneta reported 960+ women's sunglasses. Print titles
and the overlap so it can be seen what a category filter actually returns.
"""
import re, sys, time
sys.path.insert(0, "scripts")
from vinted_steals import UA, cards
import requests

s = requests.Session()
s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
s.get("https://www.vinted.it/", timeout=45)


def search(params):
    time.sleep(5)
    r = s.get("https://www.vinted.it/catalog", params=params + [("order", "newest_first")], timeout=60)
    m = re.search(r'\\?"total_entries\\?":(\d+)', r.text)
    return int(m.group(1)) if m else None, [c for c in cards(r.text) if not c["promoted"]]


runs = {
    "Gucci women's gloves (90)": [("catalog[]", 90), ("brand_ids[]", 567)],
    "Gucci men's gloves (91)": [("catalog[]", 91), ("brand_ids[]", 567)],
    "Bottega women's sunglasses (26)": [("catalog[]", 26), ("brand_ids[]", 86972)],
    "Bottega, no category": [("brand_ids[]", 86972)],
    "Bottega women's sunglasses, 'catalog_ids' spelling": [("catalog_ids[]", 26), ("brand_ids[]", 86972)],
}
got = {}
for name, p in runs.items():
    tot, cs = search(p)
    got[name] = {c["id"] for c in cs}
    print(f"\n{name}: total {tot}")
    for c in cs[:14]:
        print(f"   {c['brand'] or '':<16} {c['line2'] or '':<26} {c['title'][:50]}")
a, b = got["Gucci women's gloves (90)"], got["Gucci men's gloves (91)"]
print(f"\nGucci gloves: page-1 overlap women/men = {len(a & b)} of {len(a)}/{len(b)}")
c, d = got["Bottega women's sunglasses (26)"], got["Bottega, no category"]
print(f"Bottega: sunglasses page 1 vs no-category page 1 overlap = {len(c & d)} of {len(c)}")
