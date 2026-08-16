"""Last unknowns, at a deliberately slow pace.

Probe 2 answered the architectural question — `{"sold": true}` is a valid
filter, so sales are STATED, not inferred. But it also tripped a rate limit
partway through (429 from the 9th request onward, at ~1 req/2s), which left
three things unmeasured and taught us the real constraint: this API is much
tighter than Vinted's.

So this runs at 6-10 s between requests, ~10 requests total, and stops the
moment it sees a 429 rather than probing where the ceiling is. Finding the
exact threshold would mean deliberately hammering until it breaks, which is
both rude and unnecessary — we only need to know that a slow pace is safe.

Unmeasured, in priority order:
  1. does a search hit carry soldDate, or must we fetch each id from apiv2?
  2. what is in paginationStats (i.e. how many sold items are there really)?
  3. does the sold filter combine with createdAt, so we can scope to a window?
"""

from __future__ import annotations

import json
import random
import sys
import time

import requests

SEARCH = "https://search.vestiairecollective.com/v1/product/search"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
FIELDS = ["name", "description", "brand", "price", "link", "sold", "likes",
          "seller", "pictures", "size", "stock", "universeId", "createdAt"]
LOCALE = {"country": "IT", "currency": "EUR", "language": "it", "sizeType": "women"}
SHOES = "3"          # categoryLvl0 "Scarpe", parent 1 (women)
PRADA = "60"

S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9",
                  "Origin": "https://www.vestiairecollective.com",
                  "Referer": "https://www.vestiairecollective.com/",
                  "x-usecase": "catalog", "Content-Type": "application/json"})
STOP = False


def post(body: dict, tag: str):
    """One request, slowly. Any 429 aborts the whole probe."""
    global STOP
    if STOP:
        print(f"    {tag}: skipped (already rate-limited)")
        return None
    try:
        r = S.post(SEARCH, json=body, timeout=45)
    except requests.RequestException as exc:
        print(f"    {tag}: ERR {type(exc).__name__}")
        return None
    if r.status_code == 429:
        STOP = True
        print(f"    {tag}: HTTP 429 — backing off, stopping the probe here")
        return None
    if r.status_code != 200:
        print(f"    {tag}: HTTP {r.status_code}")
        return None
    time.sleep(random.uniform(6, 10))
    try:
        return r.json()
    except ValueError:
        return None


def body(sold=None, created_gte=None, limit=5, offset=0, facets=None):
    f = {"brand.id": [PRADA], "categoryLvl0.id": [SHOES]}
    if sold is not None:
        f["sold"] = sold
    if created_gte is not None:
        f["createdAt"] = {"gte": created_gte}
    b = {"pagination": {"offset": offset, "limit": limit}, "fields": FIELDS,
         "filters": f, "locale": LOCALE, "sort": "recency"}
    if facets:
        b["facets"] = {"fields": facets}
    return b


def main() -> int:
    print("Vestiaire — final design probe (slow pace, aborts on 429)\n")

    print("### 1. shape of a SOLD search hit")
    j = post(body(sold=True, limit=3), "sold hits")
    if j:
        its = j.get("items") or []
        print(f"    {len(its)} sold Prada shoes returned")
        if its:
            it = its[0]
            print(f"    keys: {sorted(it.keys())}")
            print(f"    has soldDate: {'soldDate' in it}")
            for k in ("id", "sold", "soldDate", "createdAt", "price", "link"):
                if k in it:
                    print(f"      {k:10} {json.dumps(it[k], ensure_ascii=False)[:100]}")

    print("\n### 2. paginationStats — how many are there?")
    j = post(body(sold=True, limit=1), "sold count")
    if j:
        print(f"    sold:  {json.dumps(j.get('paginationStats'))}")
    j = post(body(sold=False, limit=1), "live count")
    if j:
        print(f"    live:  {json.dumps(j.get('paginationStats'))}")

    print("\n### 3. does sold combine with a createdAt window?")
    for days in (7, 30):
        gte = int(time.time()) - days * 86400
        j = post(body(sold=True, created_gte=gte, limit=3), f"sold, listed <{days}d")
        if j:
            its = j.get("items") or []
            print(f"    listed within {days:>2}d and already sold: "
                  f"{json.dumps(j.get('paginationStats'))}, {len(its)} on page 1")
            for it in its[:3]:
                print(f"       id={it.get('id')} created={it.get('createdAt')} "
                      f"{str(it.get('name'))[:38]}")

    print("\n### 4. how deep does the SOLD list paginate?")
    for off in (960, 1500):
        j = post(body(sold=True, limit=5, offset=off), f"sold offset={off}")
        if j is not None:
            print(f"    offset {off}: {len(j.get('items') or [])} items")

    print("\ndone" + (" (cut short by a 429)" if STOP else " — no rate limiting at 6-10 s"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
