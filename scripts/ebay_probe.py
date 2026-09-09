"""Does the Browse API give us what the dataset needs?

Three questions the docs would not answer (the reference pages 403 on fetch):
  1. does a client-credentials token work for item_summary/search?
  2. do search results carry usable image URLs, or only thumbnails?
  3. does the Brand aspect filter actually constrain results — the thing that
     stops a visually similar Prada pump being labelled Gucci?

Credentials are read from ~/.config/ebay.env and never printed. No verbose
HTTP anywhere: a proxy password once leaked into a public CI log on this
project and that is not repeating.
"""

from __future__ import annotations

import base64, json, os, sys
import requests

ENV = os.path.expanduser("~/.config/ebay.env")
cfg = {}
for line in open(ENV):
    if "=" in line and not line.startswith("#"):
        k, v = line.strip().split("=", 1)
        cfg[k] = v
MKT = cfg.get("EBAY_MARKETPLACE", "EBAY_GB")
BASE = "https://api.ebay.com"


def token() -> str | None:
    auth = base64.b64encode(
        f"{cfg['EBAY_CLIENT_ID']}:{cfg['EBAY_CLIENT_SECRET']}".encode()).decode()
    r = requests.post(f"{BASE}/identity/v1/oauth2/token",
                      headers={"Authorization": f"Basic {auth}",
                               "Content-Type": "application/x-www-form-urlencoded"},
                      data={"grant_type": "client_credentials",
                            "scope": "https://api.ebay.com/oauth/api_scope"},
                      timeout=30)
    if r.status_code != 200:
        print(f"token failed: HTTP {r.status_code} {r.text[:200]}")
        return None
    j = r.json()
    print(f"token ok, expires in {j.get('expires_in')}s")
    return j["access_token"]


def search(tok, params, label):
    r = requests.get(f"{BASE}/buy/browse/v1/item_summary/search",
                     headers={"Authorization": f"Bearer {tok}",
                              "X-EBAY-C-MARKETPLACE-ID": MKT},
                     params=params, timeout=30)
    if r.status_code != 200:
        print(f"  {label}: HTTP {r.status_code} {r.text[:200]}")
        return None
    j = r.json()
    print(f"  {label}: total={j.get('total')}, returned={len(j.get('itemSummaries') or [])}")
    return j


def main() -> int:
    tok = token()
    if not tok:
        return 1
    print(f"marketplace {MKT}\n")

    print("1. keyword search")
    j = search(tok, {"q": "gucci ace sneakers", "limit": 5,
                     "category_ids": "3034"}, "q=gucci ace sneakers")
    if j and j.get("itemSummaries"):
        it = j["itemSummaries"][0]
        print(f"     keys: {', '.join(sorted(it.keys()))[:200]}")
        print(f"     title : {it.get('title','')[:70]}")
        print(f"     price : {(it.get('price') or {}).get('value')} "
              f"{(it.get('price') or {}).get('currency')}")
        print(f"     image : {str((it.get('image') or {}).get('imageUrl'))[:90]}")
        print(f"     thumbs: {len(it.get('thumbnailImages') or [])}")
        print(f"     addl  : {len(it.get('additionalImages') or [])}")

    print("\n2. brand aspect filter (the constraint that matters)")
    for brand in ("Gucci", "Prada"):
        search(tok, {"q": "sneakers", "limit": 3, "category_ids": "3034",
                     "aspect_filter": f"categoryId:3034,Brand:{{{brand}}}"},
               f"Brand={brand}")

    print("\n3. how deep can we page?")
    for off in (0, 200, 1000, 5000, 9999):
        r = requests.get(f"{BASE}/buy/browse/v1/item_summary/search",
                         headers={"Authorization": f"Bearer {tok}",
                                  "X-EBAY-C-MARKETPLACE-ID": MKT},
                         params={"q": "gucci", "limit": 200, "offset": off,
                                 "category_ids": "3034"}, timeout=30)
        n = len((r.json().get("itemSummaries") or [])) if r.status_code == 200 else 0
        print(f"  offset {off:>5}: HTTP {r.status_code}, {n} items")
        if r.status_code != 200:
            print(f"     {r.text[:140]}")
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
