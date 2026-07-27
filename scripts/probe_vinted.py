"""Probe the Vinted IT API through the residential proxy.

Answers three questions before we build anything:
  1. Does the anon-token bootstrap (homepage -> cookies -> /api/v2) work?
  2. What does a catalog search for "prada shoes" actually return?
  3. What does an item look like on /api/v2/items/{id}, and which field
     tells us it is SOLD vs still listed?
"""

import json
import os
import sys

import requests

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
BASE = "https://www.vinted.it"


def session():
    s = requests.Session()
    proxy = os.environ.get("PROXY_URL")
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    s.headers.update(
        {
            "User-Agent": UA,
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
        }
    )
    return s


def bootstrap(s):
    r = s.get(BASE + "/", timeout=60)
    print(f"[bootstrap] homepage HTTP {r.status_code}, {len(r.content)} bytes")
    print(f"[bootstrap] cookies: {sorted(s.cookies.keys())}")
    return r.status_code == 200


def search(s, text="prada shoes", per_page=20):
    url = BASE + "/api/v2/catalog/items"
    params = {
        "search_text": text,
        "order": "newest_first",
        "per_page": per_page,
        "page": 1,
    }
    r = s.get(url, params=params, timeout=60, headers={"Accept": "application/json"})
    print(f"[search] HTTP {r.status_code}, {len(r.content)} bytes")
    if r.status_code != 200:
        print("[search] body head:", r.text[:500])
        return []
    items = r.json().get("items", [])
    print(f"[search] {len(items)} items")
    if items:
        print("[search] FIELD NAMES on item[0]:")
        print("  " + ", ".join(sorted(items[0].keys())))
        print("[search] item[0] full JSON:")
        print(json.dumps(items[0], indent=2, ensure_ascii=False)[:4000])
    for it in items[:8]:
        price = it.get("price")
        if isinstance(price, dict):
            price = f"{price.get('amount')} {price.get('currency_code')}"
        print(
            f"  - id={it.get('id')} | {price} | {it.get('brand_title')} "
            f"| size={it.get('size_title')} | {str(it.get('title'))[:50]}"
        )
    return items


def item_detail(s, item_id):
    url = f"{BASE}/api/v2/items/{item_id}"
    r = s.get(url, timeout=60, headers={"Accept": "application/json"})
    print(f"[detail {item_id}] HTTP {r.status_code}, {len(r.content)} bytes")
    if r.status_code != 200:
        print("[detail] body head:", r.text[:500])
        return None
    item = r.json().get("item", {})
    print(f"[detail {item_id}] FIELD NAMES:")
    print("  " + ", ".join(sorted(item.keys())))
    # The fields most likely to encode sold/reserved/closed state.
    interesting = [
        k
        for k in item
        if any(
            t in k
            for t in ("closed", "hidden", "sold", "status", "reserv", "visib", "active")
        )
    ]
    print(f"[detail {item_id}] state-ish fields:")
    for k in sorted(interesting):
        print(f"    {k} = {item[k]!r}")
    return item


def main():
    s = session()
    if not bootstrap(s):
        sys.exit("bootstrap failed")
    items = search(s)
    if not items:
        sys.exit("search returned nothing")
    item_detail(s, items[0]["id"])
    print("\n[probe] done")


if __name__ == "__main__":
    main()
