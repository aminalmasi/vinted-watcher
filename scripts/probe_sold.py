"""Find a reliable SOLD signal for a Vinted IT listing.

The catalog feed hides sold items, so "it vanished from the feed" is only a
*candidate*. We need a confirmation step. This probe tests the options:
  A. item_box / status fields already present in the search payload
  B. /api/v2/items/{id} JSON (several header variants)
  C. the item's public web page, scanned for sold markers
"""

import json
import os
import re

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
    s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9,en;q=0.8"})
    s.get(BASE + "/", timeout=60)
    return s


def search(s, text, per_page=20, extra=None):
    params = {"search_text": text, "order": "newest_first", "per_page": per_page, "page": 1}
    params.update(extra or {})
    r = s.get(
        BASE + "/api/v2/catalog/items",
        params=params,
        timeout=60,
        headers={"Accept": "application/json", "Referer": BASE + "/catalog"},
    )
    if r.status_code != 200:
        print(f"  search HTTP {r.status_code}")
        return []
    return r.json().get("items", [])


def main():
    s = session()

    print("=== A. what search gives us per item ===")
    items = search(s, "prada shoes")
    it0 = items[0]
    for k in ("id", "title", "status", "is_visible", "item_box", "conversion", "content_source"):
        print(f"  {k} = {json.dumps(it0.get(k), ensure_ascii=False)}")

    print("\n=== A2. does the feed EVER include sold items? (status_ids filter) ===")
    # Vinted status_ids: 6=new w/ tags, 1=new w/o tags, 2=very good, 3=good, 4=satisfactory
    # There is no public 'sold' status id; test the dedicated flag instead.
    for extra in ({"status_ids[]": "0"}, {"is_sold": "true"}, {"sold": "true"}):
        got = search(s, "prada shoes", per_page=5, extra=extra)
        boxes = {json.dumps(i.get("item_box", {}).get("first_line"), ensure_ascii=False) for i in got}
        print(f"  {extra} -> {len(got)} items, first_line values: {sorted(boxes)[:5]}")

    print("\n=== B. /api/v2/items/{id} header variants ===")
    iid = it0["id"]
    variants = [
        ("plain json", {"Accept": "application/json"}),
        (
            "browser-ish",
            {
                "Accept": "application/json, text/plain, */*",
                "Referer": it0["url"],
                "X-Requested-With": "XMLHttpRequest",
                "x-anon-id": s.cookies.get("anon_id", ""),
            },
        ),
    ]
    for name, hdrs in variants:
        r = s.get(f"{BASE}/api/v2/items/{iid}", headers=hdrs, timeout=60)
        ct = r.headers.get("content-type", "")
        print(f"  [{name}] HTTP {r.status_code} ct={ct.split(';')[0]} {len(r.content)}B")
        if r.status_code == 200 and "json" in ct:
            item = r.json().get("item", {})
            print("    keys:", ", ".join(sorted(item.keys()))[:600])
            for k in sorted(item):
                if any(t in k for t in ("closed", "hidden", "sold", "status", "reserv", "visib")):
                    print(f"      {k} = {item[k]!r}")

    print("\n=== C. item web page markers ===")
    r = s.get(it0["url"], timeout=60)
    html = r.text
    print(f"  HTTP {r.status_code}, {len(html)}B")
    for pat in ("Venduto", "venduto", "is_closed", "isClosed", "is_sold", '"sold"', "Non disponibile"):
        n = html.count(pat)
        if n:
            print(f"    marker {pat!r} x{n}")
    m = re.search(r'"item_closing_action"\s*:\s*("?[^,}]+)', html)
    if m:
        print(f"    item_closing_action = {m.group(1)}")
    for key in ("is_closed", "is_hidden", "is_reserved", "item_closing_action"):
        for m in re.finditer(rf'"{key}"\s*:\s*("?[^,}}]+)', html):
            print(f"    page JSON {key} = {m.group(1)}")
            break

    print("\n[probe] done")


if __name__ == "__main__":
    main()
