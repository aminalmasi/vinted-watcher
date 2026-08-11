"""Which fields can we get WITHOUT touching a blocked item page?

Images are solved: the CDN serves the cluster directly, no proxy, 61 ms. What
is not solved is the seller's free-text description, which the catalog feed may
or may not carry. If it does, the whole archive can be built from data we
already fetch, and no extra requests to Vinted are needed at all.
"""

import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vintedwatch.client import BASE, VintedClient  # noqa: E402

WANTED = ("description", "photos", "photo", "url", "brand", "size", "status",
          "price", "created", "user")


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(message)s")
    state = json.load(open("data/state.json"))
    client = VintedClient(token_cache=state.get("token"))

    print("=== A. catalog feed item — every field ===")
    items = client.search({"search_text": "prada shoes", "order": "newest_first",
                           "per_page": 5}, page=1) or []
    if not items:
        sys.exit("feed failed")
    it = items[0]
    print("  keys:", ", ".join(sorted(it.keys())))
    print(f"  has 'description': {'description' in it}")
    photos = it.get("photos") or []
    print(f"  photos: {len(photos)}")
    if photos:
        p = photos[0]
        print(f"    photo keys: {', '.join(sorted(p.keys()))}")
        print(f"    full_size_url: {str(p.get('full_size_url'))[:95]}")
        print(f"    biggest thumb: {str((p.get('thumbnails') or [{}])[-1].get('url'))[:95]}")

    print("\n=== B. wardrobe item — every field ===")
    uid = (it.get("user") or {}).get("id")
    r = client._get(f"{BASE}/api/v2/wardrobe/{uid}/items",
                    params={"page": 1, "per_page": 3}, tries=2,
                    headers={"Accept": "application/json", "Referer": BASE + "/"})
    if r is not None and r.status_code == 200:
        w = (r.json().get("items") or [{}])[0]
        print("  keys:", ", ".join(sorted(w.keys())))
        print(f"  has 'description': {'description' in w}")
        if w.get("description"):
            print(f"    sample: {str(w['description'])[:160]!r}")
    else:
        print(f"  wardrobe -> HTTP {r.status_code if r is not None else 'none'}")

    print("\n=== C. is there any item endpoint that returns a description? ===")
    iid = it["id"]
    for path in (f"/api/v2/items/{iid}", f"/api/v2/items/{iid}/details",
                 f"/api/v2/catalog/items/{iid}"):
        rr = client._get(BASE + path, tries=1,
                         headers={"Accept": "application/json", "Referer": BASE + "/"})
        code = rr.status_code if rr is not None else "none"
        has = ""
        if rr is not None and rr.status_code == 200 and "json" in rr.headers.get("content-type", ""):
            has = f"  description={'description' in rr.text}"
        print(f"  {path} -> HTTP {code}{has}")

    print(f"\n[probe] traffic {client.bytes_uncompressed/1024:.0f} KB")


if __name__ == "__main__":
    main()
