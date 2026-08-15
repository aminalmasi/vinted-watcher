"""Everything we still need to know to design a Vestiaire watcher, in one run.

Probe 1 settled access: search + images answer a plain GitHub Actions IP with no
proxy at all. Only the university IP is walled off. So the remaining questions
are about the search API's shape, and each one changes the design:

  1. can we filter to SOLD items directly?  -> if yes, no disappearance
     inference is needed at all, and the whole Vinted architecture collapses
     into "poll the sold list".
  2. facet counts                            -> how big is the watch set per
     brand, i.e. what does a sweep cost.
  3. the shoes category id                   -> so we track shoes, not handbags.
  4. pagination depth                        -> Vinted capped every query at 960
     results; if Vestiaire does the same, enumeration must be sliced.
  5. page size + bytes                       -> the traffic budget.
  6. is soldDate in search results           -> or must we fetch per id.

Paced and small: a few dozen requests, well under what a single human browsing
the site would generate.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time

import requests

SEARCH = "https://search.vestiairecollective.com/v1/product/search"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
FIELDS = ["name", "description", "brand", "model", "country", "price", "discount",
          "link", "sold", "likes", "editorPicks", "shouldBeGone", "seller",
          "directShipping", "local", "pictures", "colors", "size", "stock",
          "universeId", "createdAt"]
LOCALE = {"country": "IT", "currency": "EUR", "language": "it", "sizeType": "women"}
BRANDS = {"Prada": "60", "Miu Miu": "117", "Maison Martin Margiela": "62",
          "Christian Louboutin": "236", "Salvatore Ferragamo": "186"}

S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9",
                  "Origin": "https://www.vestiairecollective.com",
                  "Referer": "https://www.vestiairecollective.com/",
                  "x-usecase": "catalog", "Content-Type": "application/json"})
TRAFFIC = 0


def post(body: dict, tag: str = "") -> tuple[int, dict, int]:
    global TRAFFIC
    try:
        r = S.post(SEARCH, json=body, timeout=45)
    except requests.RequestException as exc:
        print(f"    ERR {type(exc).__name__} {tag}")
        return 0, {}, 0
    n = len(r.content)
    TRAFFIC += n
    time.sleep(random.uniform(1.5, 3.0))
    if r.status_code != 200:
        return r.status_code, {}, n
    try:
        return 200, r.json(), n
    except ValueError:
        return 200, {}, n


def items_of(j: dict) -> list:
    return j.get("items") or j.get("products") or j.get("data") or []


def base(limit=5, offset=0, brand="60", extra_filters=None, facets=None):
    b = {"pagination": {"offset": offset, "limit": limit},
         "fields": FIELDS,
         "filters": {"brand.id": [brand], **(extra_filters or {})},
         "locale": LOCALE,
         "sort": "recency"}
    if facets:
        b["facets"] = {"fields": facets}
    return b


def q1_sold_filter():
    print("\n### 1. can we ask for SOLD items directly?")
    for label, filt in [
            ('sold: [true]',        {"sold": [True]}),
            ('sold: ["true"]',      {"sold": ["true"]}),
            ('sold: true',          {"sold": True}),
            ('stock: [0]',          {"stock": [0]}),
    ]:
        code, j, n = post(base(limit=5, extra_filters=filt), label)
        its = items_of(j)
        if code != 200:
            print(f"    {label:22} HTTP {code}")
            continue
        flags = [i.get("sold") for i in its]
        print(f"    {label:22} {len(its)} items  sold flags={flags}")


def q2_facets():
    print("\n### 2. facet counts per brand (how big is the watch set?)")
    code, j, n = post(base(limit=1, facets=["brand", "categoryLvl0", "categoryLvl1",
                                            "sold", "stock"]), "facets")
    if code != 200:
        print(f"    HTTP {code}")
        return
    f = j.get("facets") or j.get("aggregations") or {}
    print(f"    facet keys: {list(f)[:12]}")
    print(f"    raw (trimmed): {json.dumps(f, ensure_ascii=False)[:1200]}")
    for k in ("total", "totalHits", "count", "numFound"):
        if k in j:
            print(f"    {k} = {j[k]}")
    print(f"    top-level keys: {list(j)[:14]}")


def q3_categories():
    print("\n### 3. shoe category ids, per brand totals")
    for name, bid in BRANDS.items():
        code, j, n = post(base(limit=1, brand=bid,
                               facets=["categoryLvl0", "categoryLvl1"]), name)
        if code != 200:
            print(f"    {name:24} HTTP {code}")
            continue
        f = j.get("facets") or {}
        blob = json.dumps(f, ensure_ascii=False)
        shoes = [w for w in ("Scarpe", "Shoes", "Chaussures") if w in blob]
        tot = j.get("total") or j.get("totalHits") or j.get("count")
        print(f"    {name:24} total={tot}  shoe-words={shoes}  facet={blob[:220]}")


def q4_depth():
    print("\n### 4. how deep can pagination go? (Vinted capped at 960)")
    for off in (0, 480, 960, 2000, 10000):
        code, j, n = post(base(limit=5, offset=off), f"offset={off}")
        its = items_of(j)
        print(f"    offset {off:>6} -> HTTP {code}, {len(its)} items, {n/1024:.0f} KB")
        if code == 200 and not its:
            print("      (empty — this is the cap)")
            break


def q5_page_size():
    print("\n### 5. max page size and bytes per page")
    for lim in (48, 96, 200, 500):
        code, j, n = post(base(limit=lim), f"limit={lim}")
        its = items_of(j)
        print(f"    limit {lim:>4} -> HTTP {code}, {len(its)} returned, "
              f"{n/1024:.0f} KB ({n/max(len(its),1)/1024:.1f} KB/item)")


def q6_shape():
    print("\n### 6. what a search result actually contains")
    code, j, n = post(base(limit=3), "shape")
    its = items_of(j)
    if not its:
        print(f"    HTTP {code}, nothing returned; top-level={list(j)[:12]}")
        return
    it = its[0]
    print(f"    item keys: {sorted(it.keys())}")
    for k in ("id", "sold", "soldDate", "createdAt", "price", "link", "brand", "stock"):
        if k in it:
            print(f"      {k:10} {json.dumps(it[k], ensure_ascii=False)[:110]}")
    print(f"    has soldDate: {'soldDate' in it}")


def main() -> int:
    print("Vestiaire search API — design probe")
    print(f"proxy: {'set' if os.environ.get('PROXY_URL') else 'none (direct)'}")
    for fn in (q1_sold_filter, q2_facets, q3_categories, q4_depth, q5_page_size, q6_shape):
        try:
            fn()
        except Exception as exc:  # a probe must never die half-way
            print(f"    !! {type(exc).__name__}: {exc}")
    print(f"\ntotal traffic this probe: {TRAFFIC/1024/1024:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
