"""Learn how a sold listing is encoded, without needing to find one live.

Two angles:
  1. Ask the wardrobe/catalog APIs for sold items explicitly — some Vinted
     deployments accept a status filter.
  2. Read the item page's own JS: the bundle that renders the sold badge has to
     name the states it switches on, so the enum values are in there.
"""

import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vintedwatch.client import BASE, VintedClient  # noqa: E402

BROWSER = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": BASE + "/catalog",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
}


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(message)s")
    client = VintedClient()
    client.bootstrap()
    feed = client.search({"search_text": "prada shoes", "order": "newest_first", "per_page": 24})
    if not feed:
        sys.exit("feed failed")
    item, uid = feed[0], feed[0]["user"]["id"]

    print("=== 1. can any endpoint be asked for SOLD items? ===")
    variants = [
        (f"/api/v2/wardrobe/{uid}/items", {"page": 1, "per_page": 20, "status": "sold"}),
        (f"/api/v2/wardrobe/{uid}/items", {"page": 1, "per_page": 20, "is_closed": "true"}),
        (f"/api/v2/wardrobe/{uid}/items", {"page": 1, "per_page": 20, "include_sold": "true"}),
        ("/api/v2/catalog/items", {"search_text": "prada shoes", "per_page": 20, "status": "sold"}),
    ]
    for path, params in variants:
        r = client._get(BASE + path, params=params, tries=1,
                        headers={"Accept": "application/json", "Referer": BASE + "/"})
        if r is None or r.status_code != 200:
            print(f"  {path} {list(params)[-1]} -> HTTP {r.status_code if r else 'none'}")
            continue
        items = r.json().get("items", [])
        closed = [i for i in items if i.get("is_closed")]
        print(f"  {path} {list(params)[-1]} -> {len(items)} items, {len(closed)} closed")
        if closed:
            print(f"      EXAMPLE SOLD ITEM: {closed[0].get('id')} {closed[0].get('url')}")

    print("\n=== 2. what does the page's own JS say about closing actions? ===")
    r = client._get(f"{BASE}/items/{item['id']}", tries=2, retry_statuses=(403,), headers=BROWSER)
    if r is None or r.status_code != 200:
        sys.exit(f"item page HTTP {r.status_code if r else 'none'}")
    html = r.text

    # Enum-ish strings the renderer switches on.
    for pat in (r"item_closing_action[^,}]{0,80}",
                r"closing_action[\"']?\s*[:=]\s*[\"'][a-z_]+[\"']",
                r"[\"'](sold|deleted|reserved|closed_by_admin|swapped)[\"']\s*[:=]",
                r"itemClosingAction[^,}]{0,60}"):
        hits = sorted({m.group(0)[:100] for m in re.finditer(pat, html)})
        print(f"  /{pat[:38]}/ -> {len(hits)} distinct")
        for h in hits[:8]:
            print(f"      {h}")

    # Where the sold badge text lives, and what guards it.
    for needle in ("Venduto", "venduto"):
        for m in list(re.finditer(needle, html))[:3]:
            lo, hi = max(0, m.start() - 130), m.end() + 130
            print(f"  ...{html[lo:hi]}...".replace("\n", " ")[:280])

    print(f"\n[probe] traffic {client.bytes_uncompressed / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
