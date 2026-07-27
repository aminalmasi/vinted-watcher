"""Find a confirmation path that Vinted does not 403.

The catalog API works fine from our exits; the item *page* started returning
403. This tries the plausible alternatives against a live listing taken from
the feed, so we can see which survive.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vintedwatch.client import BASE, VintedClient  # noqa: E402

BROWSER = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": BASE + "/catalog",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}


def show(tag, r):
    if r is None:
        print(f"  {tag:38s} -> NO RESPONSE")
        return None
    ct = r.headers.get("content-type", "?").split(";")[0]
    print(f"  {tag:38s} -> HTTP {r.status_code} {ct} {len(r.content)}B")
    return r


def main():
    c = VintedClient()
    print("=== 0. fresh bootstrap, then the feed ===")
    c.bootstrap()
    items = c.search({"search_text": "prada shoes", "order": "newest_first", "per_page": 24})
    if not items:
        sys.exit("feed failed")
    it = items[0]
    iid, url = it["id"], it["url"]
    print(f"  live item {iid}: {url}")

    print("\n=== A. item HTML immediately after a fresh bootstrap ===")
    show("GET /items/{id} (browser headers)", c._get(url, tries=1, headers=BROWSER))

    print("\n=== B. item JSON API variants ===")
    hdrs_json = {
        "Accept": "application/json, text/plain, */*",
        "Referer": url,
        "X-Requested-With": "XMLHttpRequest",
    }
    for path in (f"/api/v2/items/{iid}", f"/api/v2/items/{iid}/details", f"/api/v2/items/{iid}/plugins"):
        r = show(path, c._get(BASE + path, tries=1, headers=hdrs_json))
        if r is not None and r.status_code == 200 and "json" in r.headers.get("content-type", ""):
            body = r.json()
            item = body.get("item", body)
            if isinstance(item, dict):
                state = {k: v for k, v in item.items()
                         if any(t in k for t in ("closed", "hidden", "sold", "reserv", "status"))}
                print(f"      state fields: {json.dumps(state, ensure_ascii=False)[:300]}")

    print("\n=== C. does a bare HEAD survive? (tells removed vs exists, not sold) ===")
    try:
        r = c.session.head(url, timeout=(15, 45), allow_redirects=True, headers=BROWSER)
        print(f"  HEAD -> HTTP {r.status_code}")
    except Exception as exc:
        print(f"  HEAD failed: {type(exc).__name__}: {exc}")

    print("\n=== D. is the 403 sticky, or does a new exit clear it? ===")
    for attempt in range(3):
        c.session.close()
        c._next_gateway()
        time.sleep(3)
        show(f"retry {attempt + 1} on a new exit", c._get(url, tries=1, headers=BROWSER))

    print("\n=== E. same item via the .com domain ===")
    show("vinted.com/items/{id}", c._get(f"https://www.vinted.com/items/{iid}", tries=1, headers=BROWSER))

    print(f"\n[probe] traffic {c.bytes_uncompressed / 1024:.0f} KB uncompressed")


if __name__ == "__main__":
    main()
