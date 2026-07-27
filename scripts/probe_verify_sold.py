"""Validate the sold-detection logic against reality.

Two doubts to settle:
  1. `_STATE_RE` takes the FIRST match in a 2.4 MB page. Is that the item's own
     data, or some unrelated default earlier in the bundle? Print EVERY match.
  2. The 'sold' branch has never fired. Find a genuinely sold listing and check
     that check_sold() actually returns 'sold' for it.

Sold listings are found via seller closets: a member page lists that seller's
items including the ones already sold.
"""

import json
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vintedwatch.client import BASE, _STATE_RE, VintedClient  # noqa: E402

BROWSER = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": BASE + "/catalog",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
}
ITEM_LINK = re.compile(r"/items/(\d+)-")


def dump_all_matches(html, label):
    """Every occurrence of each state key, not just the first."""
    print(f"    --- all state-key occurrences in {label} ---")
    for key, rx in _STATE_RE.items():
        hits = [m.group(1) for m in rx.finditer(html)]
        uniq = sorted(set(hits))
        flag = "  <-- AMBIGUOUS" if len(uniq) > 1 else ""
        print(f"      {key:22s} x{len(hits):<3d} values={uniq[:6]}{flag}")


def inspect(client, item_id, label):
    url = f"{BASE}/items/{item_id}"
    r = client._get(url, tries=2, retry_statuses=(403,), headers=BROWSER)
    if r is None:
        print(f"  [{label}] {item_id}: NO RESPONSE")
        return None
    if r.status_code != 200:
        print(f"  [{label}] {item_id}: HTTP {r.status_code}")
        return r.status_code
    html = r.text
    verdict = client.check_sold(item_id, url)
    print(f"  [{label}] {item_id}: check_sold()={verdict}")
    dump_all_matches(html, f"item {item_id}")
    # What does the page say in its own words, near the item state?
    for needle in ('"item_closing_action"', '"is_closed"'):
        i = html.find(needle)
        if i != -1:
            print(f"      context {needle}: ...{html[i:i + 160]}...")
    return verdict


def main():
    client = VintedClient()
    client.bootstrap()

    feed = client.search({"search_text": "prada shoes", "order": "newest_first", "per_page": 24})
    if not feed:
        sys.exit("feed failed")
    live_ids = {i["id"] for i in feed}
    print(f"feed has {len(feed)} live listings\n")

    print("=== 1. a listing we KNOW is live (it is in the feed right now) ===")
    inspect(client, feed[0]["id"], "LIVE")

    print("\n=== 2. hunting for a genuinely SOLD listing in seller closets ===")
    sold_found = []
    for seller in feed[:4]:
        uid = seller["user"]["id"]
        r = client._get(f"{BASE}/member/{uid}", tries=2, retry_statuses=(403,), headers=BROWSER)
        if r is None or r.status_code != 200:
            print(f"  member/{uid}: HTTP {r.status_code if r else 'none'}")
            continue
        ids = []
        for m in ITEM_LINK.finditer(r.text):
            iid = int(m.group(1))
            if iid not in live_ids and iid not in ids:
                ids.append(iid)
        print(f"  member/{uid}: {len(ids)} candidate item ids not in the live feed")
        for iid in ids[:3]:
            v = inspect(client, iid, "CANDIDATE")
            if v == "sold":
                sold_found.append(iid)
        if sold_found:
            break

    print(f"\n=== RESULT: sold listings correctly identified: {sold_found or 'NONE FOUND'} ===")
    print(f"[probe] traffic {client.bytes_uncompressed / 1024 / 1024:.1f} MB uncompressed")


if __name__ == "__main__":
    main()
