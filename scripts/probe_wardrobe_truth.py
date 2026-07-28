"""Can the JSON API confirm a sale, now that .it HTML is blocked?

We know two listings that really sold (verified 2026-07-27 from their item
pages). Their sellers are known too. So ask the wardrobe API about them and see
whether it reports the sale — if it does, confirmation can move off the 2.4 MB
HTML page onto a small JSON call that still works from our exits.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vintedwatch.client import BASE, VintedClient  # noqa: E402

# item_id -> seller_id, taken from the confirmed-sold pages.
KNOWN_SOLD = {9505849905: 32001697, 9493035670: 3138688371}


def wardrobe_page(client, uid, page):
    r = client._get(f"{BASE}/api/v2/wardrobe/{uid}/items",
                    params={"page": page, "per_page": 40}, tries=2,
                    headers={"Accept": "application/json", "Referer": BASE + "/"})
    if r is None:
        return None, "no response"
    if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
        return None, f"HTTP {r.status_code}"
    return r.json().get("items", []), None


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    client = VintedClient()
    # Deliberately NOT bootstrapping: prove this works with the cached token
    # alone, since minting a new .it token is what is currently blocked.
    print(f"has cached token: {client.has_token}\n")

    for item_id, uid in KNOWN_SOLD.items():
        print(f"=== sold item {item_id} (seller {uid}) ===")
        found = None
        scanned = 0
        for page in range(1, 6):
            items, err = wardrobe_page(client, uid, page)
            if err:
                print(f"  page {page}: {err}")
                break
            if not items:
                break
            scanned += len(items)
            for it in items:
                if it.get("id") == item_id:
                    found = it
                    break
            if found:
                break
        if found:
            flags = {k: found.get(k) for k in
                     ("is_closed", "is_hidden", "is_reserved", "is_draft", "is_processing")}
            print(f"  FOUND after scanning {scanned}: {flags}")
            print(f"  -> wardrobe {'REPORTS THE SALE' if found.get('is_closed') else 'says still live (useless)'}")
        else:
            print(f"  NOT in wardrobe (scanned {scanned}) -> sold items are hidden from it")

    # Also re-read one sold item's page to see whether .it HTML is truly gone.
    print("\n=== is the .it item page still blocked? ===")
    r = client._get(f"{BASE}/items/9505849905", tries=1,
                    headers={"Accept": "text/html", "Referer": BASE + "/catalog"})
    print(f"  .it item page -> HTTP {r.status_code if r else 'no response'}")

    print(f"\n[probe] traffic {client.bytes_uncompressed / 1024:.0f} KB")


if __name__ == "__main__":
    main()
