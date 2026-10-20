"""Does one Vinted search accept several categories at once?

The app's filter lets you pick one category. The website's search URL takes
`catalog[]=` as a list, so this checks what Vinted actually returns:

  each category alone, then the categories together, compared by listing id.
  Together = the union of the singles  -> several categories in one filter work
  Together = only one of them          -> Vinted ignores all but one
"""
import sys, time
sys.path.insert(0, "scripts")
from vinted_steals import UA, cards
import re, requests

s = requests.Session()
s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
s.get("https://www.vinted.it/", timeout=45)


def search(cats, brands):
    time.sleep(5)
    params = [("catalog[]", c) for c in cats] + [("brand_ids[]", b) for b in brands] + [("order", "newest_first")]
    r = s.get("https://www.vinted.it/catalog", params=params, timeout=60)
    m = re.search(r'\\?"total_entries\\?":(\d+)', r.text)
    ids = {c["id"] for c in cards(r.text) if not c["promoted"]}
    return r.status_code, int(m.group(1)) if m else None, ids


# Small categories so the totals stay below Vinted's 960 cap and can be added.
tests = [
    ("Hermes: women's sunglasses + handbags", [26, 156], [4785]),
    ("Bottega Veneta: women's sunglasses + men's sunglasses + women's belts", [26, 98, 20], [86972]),
    ("Gucci: women's gloves + men's gloves", [90, 91], [567]),
]
for name, cats, brands in tests:
    singles = {c: search([c], brands) for c in cats}
    code, tot, both = search(cats, brands)
    union = set().union(*(v[2] for v in singles.values()))
    print(f"\n{name}  (HTTP {code})")
    for c, (sc, st, ids) in singles.items():
        print(f"  category {c:>4} alone: {st} listings, {len(ids)} on page 1, "
              f"{len(ids & both)} of those appear in the combined search")
    print(f"  combined: {tot} listings (sum of singles {sum(v[1] or 0 for v in singles.values())}), "
          f"{len(both)} on page 1, {len(both - union)} not in any single search")
