"""Why do live listings go missing from the steals sweep?

After the category split, 18 checked "absent" listings were still live, 13
of them in Kids / Home / Hobbies / Sport - the 'other' group, which searches
seven top-level categories at once. Two explanations, tested separately:

  A  a search over top-level (root) categories does not return everything
     under them. For each missed listing: is it found by a search of its own
     leaf category? by the seven-root group? by its root alone?
  B  feed shift: a listing sold or deleted between two page requests moves
     every later listing up one place, so the first listing of the next page
     slides onto a page already read. Read the same feed twice in a row and
     count listings one read sees inside the span the other covered.
"""
import json, re, sys, time
sys.path.insert(0, "scripts")
from vinted_steals import UA, cards, CAT_GROUPS
import requests

s = requests.Session()
s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
s.get("https://www.vinted.it/", timeout=45)
brands = json.load(open("data/steals/brands.json"))
nodes = {n["id"]: n for n in json.load(open("data/catalogs.json"))}


def root_of(cid):
    n = nodes.get(cid)
    while n and n["parentId"] != -1:
        n = nodes.get(n["parentId"])
    return n["id"] if n else None


def find(iid, cats, ids, pages=6):
    """Page newest-first until passing iid; return the page it was on, or None."""
    params = [("catalog[]", c) for c in cats] + [("brand_ids[]", b) for b in ids] + [("order", "newest_first")]
    for p in range(1, pages + 1):
        time.sleep(5)
        cs = cards(s.get("https://www.vinted.it/catalog", params=params + [("page", p)], timeout=60).text)
        got = [c["id"] for c in cs]
        if iid in got:
            return p
        org = [c["id"] for c in cs if not c["promoted"]]
        if not org or max(org) < iid:
            return None
    return None


st = json.load(open("data/steals/state.json"))
last = st["cycles"][-1]["at"]
missed = [r for r in st["watch"].values() if r.get("checks", 0) > 0 and r.get("catalog_id")
          and r.get("checked_at", 0) >= last - 3600][:8]
other = dict(CAT_GROUPS)["other"]
print("A: where can a missed listing be found?")
for r in missed:
    iid, ids, cid = int(r["id"]), brands[r["group"]], r["catalog_id"]
    root = root_of(cid)
    grp = other if root in other else [g for _, g in CAT_GROUPS if root in g or any(root_of(x) == root for x in g)][0] if root else []
    leaf = find(iid, [cid], ids)
    root_alone = find(iid, [root], ids) if root else None
    group = find(iid, other, ids) if root in other else "n/a"
    print(f"  {iid} {r['group']:<12} cat {cid} root {root}: leaf-search page {leaf}, "
          f"root-alone page {root_alone}, 7-root group page {group}", flush=True)

print("\nB: the same feed read twice in a row")
ids = brands["Gucci"]
params = [("catalog[]", 1187), ("catalog[]", 146)] + [("brand_ids[]", b) for b in ids] + [("order", "newest_first")]
reads = []
for k in range(2):
    seen = []
    for p in range(1, 5):
        time.sleep(5)
        seen += [c["id"] for c in cards(s.get("https://www.vinted.it/catalog", params=params + [("page", p)], timeout=60).text) if not c["promoted"]]
    reads.append(seen)
a, b = reads
lo = max(min(a), min(b))
A, B = {x for x in a if x >= lo}, {x for x in b if x >= lo}
print(f"  read 1: {len(A)} listings in the shared span, read 2: {len(B)}; "
      f"in read 1 only: {len(A - B)}, in read 2 only: {len(B - A)}, duplicates within read 1: {len(a) - len(set(a))}, read 2: {len(b) - len(set(b))}")
