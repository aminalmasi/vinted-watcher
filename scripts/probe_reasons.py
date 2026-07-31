"""Find a cheap, unblocked way to tell SOLD from HIDDEN from REMOVED.

Only the item page ever carried that distinction (`item_closing_action`), and
.it HTML was blocked on 2026-07-28. Four days on, test every angle:

  A. Has the .it block decayed? Cheapest possible win.
  B. Does a bare HEAD get through? Status alone separates removed (404) from
     everything else, for almost no bytes.
  C. Vinted runs Next.js, so /_next/data/<buildId>/<locale>/items/<id>.json may
     return the page props as a small JSON — definitive AND tiny.
  D. Does vinted.fr render the item_status block for an Italian listing?
     (.com does not.)
  E. Can a Range request pull just the slice of HTML holding the state?
"""

import json
import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vintedwatch.client import BASE, VintedClient  # noqa: E402

SOLD_ITEM = 9505849905          # confirmed sold 2026-07-27
BROWSER = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": BASE + "/catalog",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
}


def state_of(html, item_id):
    """Pull the item_status block for this listing, if present."""
    anchor = html.find(f'\\"item_id\\":{item_id}')
    if anchor == -1:
        anchor = html.find(f'"item_id":{item_id}')
    if anchor == -1:
        return None
    w = html[max(0, anchor - 500):anchor + 1200]
    out = {}
    for key in ("is_closed", "is_hidden", "is_reserved", "item_closing_action"):
        m = re.search(rf'\\?"{key}\\?"\s*:\s*(\\?"[^",}}]*\\?"|true|false|null)', w)
        if m:
            out[key] = m.group(1).strip('\\"')
    return out or None


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(message)s")
    state = json.load(open("data/state.json"))
    client = VintedClient(token_cache=state.get("token"))

    live = client.search({"search_text": "prada shoes", "order": "newest_first",
                          "per_page": 24}, page=1) or []
    live_id = live[0]["id"] if live else None
    print(f"known SOLD: {SOLD_ITEM}   known LIVE: {live_id}\n")

    print("=== A. is the .it HTML block gone? ===")
    for label, iid in (("SOLD", SOLD_ITEM), ("LIVE", live_id)):
        if not iid:
            continue
        r = client._get(f"{BASE}/items/{iid}", tries=1, headers=BROWSER)
        code = r.status_code if r is not None else "no response"
        print(f"  {label} item page -> HTTP {code}"
              f"{f', {len(r.content)//1024} KB' if r is not None and r.status_code == 200 else ''}")
        if r is not None and r.status_code == 200:
            print(f"      state: {state_of(r.text, iid)}")

    print("\n=== B. does a bare HEAD get through? (separates removed) ===")
    for label, iid in (("SOLD", SOLD_ITEM), ("LIVE", live_id), ("BOGUS", 1234567890)):
        if not iid:
            continue
        try:
            r = client.session.head(f"{BASE}/items/{iid}", timeout=(15, 45),
                                    allow_redirects=False, headers=BROWSER)
            print(f"  {label:5s} -> HTTP {r.status_code}  {r.headers.get('content-length', '?')} bytes")
        except Exception as exc:
            print(f"  {label:5s} -> {type(exc).__name__}")

    print("\n=== C. Next.js data endpoint (small JSON, would be ideal) ===")
    build = None
    for src in (f"{BASE}/items/{SOLD_ITEM}", "https://www.vinted.fr/", "https://www.vinted.com/"):
        r = client._get(src, tries=1, headers=BROWSER)
        if r is not None and r.status_code == 200:
            m = re.search(r'"buildId":"([^"]+)"', r.text)
            if m:
                build = m.group(1)
                print(f"  buildId {build} (from {src})")
                break
    if build:
        for locale in ("it", ""):
            path = f"/_next/data/{build}/{locale}/items/{SOLD_ITEM}.json".replace("//", "/")
            r = client._get(BASE + path, tries=1, headers={"Accept": "application/json"})
            code = r.status_code if r is not None else "no response"
            size = f", {len(r.content)//1024} KB" if r is not None else ""
            print(f"  {path} -> HTTP {code}{size}")
            if r is not None and r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                blob = json.dumps(r.json())
                print(f"      contains item_closing_action: {'item_closing_action' in blob}")
    else:
        print("  could not read a buildId from any page")

    print("\n=== D. does vinted.fr carry the state for an IT listing? ===")
    r = client._get(f"https://www.vinted.fr/items/{SOLD_ITEM}", tries=1, headers=BROWSER)
    if r is not None and r.status_code == 200:
        print(f"  fr item page -> HTTP 200, {len(r.content)//1024} KB, "
              f"state: {state_of(r.text, SOLD_ITEM)}")
    else:
        print(f"  fr item page -> HTTP {r.status_code if r is not None else 'no response'}")

    print("\n=== E. Range request — pay for a slice, not 2.4 MB ===")
    r = client._get(f"{BASE}/items/{SOLD_ITEM}", tries=1,
                    headers=dict(BROWSER, **{"Range": "bytes=0-399999"}))
    if r is not None:
        print(f"  HTTP {r.status_code} ({len(r.content)//1024} KB) "
              f"content-range={r.headers.get('content-range', 'none')}")
        if r.status_code in (200, 206):
            print(f"      state in slice: {state_of(r.text, SOLD_ITEM)}")

    print(f"\n[probe] traffic {client.bytes_uncompressed / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
