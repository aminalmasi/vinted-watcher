"""Which category combinations does one Vinted search honour?

Seven top-level categories in one search lost listings that each top-level
category alone returned on page 1. Leaf categories combined fine. So test the
combinations the steals sweep would use, against each part alone, by id:
does the combined first page contain the newest listings of every part?
"""
import sys, time
sys.path.insert(0, "scripts")
from vinted_steals import UA, cards
import requests

s = requests.Session()
s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
s.get("https://www.vinted.it/", timeout=45)
GUCCI = [567]


def page1(cats):
    time.sleep(5)
    params = [("catalog[]", c) for c in cats] + [("brand_ids[]", b) for b in GUCCI] + [("order", "newest_first")]
    return [c["id"] for c in cards(s.get("https://www.vinted.it/catalog", params=params, timeout=60).text)
            if not c["promoted"]]


tests = {
    "two roots: Kids + Home": [1193, 1918],
    "two roots: Hobbies + Sport": [4824, 4332],
    "root + level-2: Designer + Kids girls": [2993, 1195],
    "level-2 pair: women's accessories + beauty": [1187, 146],
    "level-2 x4 from different roots: Kids girls, Kids boys, Home textiles, Home accessories": [1195, 1194, 1919, 1934],
}
for name, cats in tests.items():
    alone = {c: page1([c]) for c in cats}
    both = set(page1(cats))
    # The combined newest-first page should hold every part's listings that
    # are newer than the combined page's oldest one.
    floor = min(both) if both else 0
    print(f"\n{name}")
    for c, ids in alone.items():
        due = [i for i in ids if i >= floor]
        print(f"  {c:>5}: {len(due)} of its page-1 listings are newer than the combined page's oldest; "
              f"{sum(i in both for i in due)} of them are in the combined page")
