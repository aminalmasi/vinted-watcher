"""Find a reliable SOLD signal for a Vinted IT listing.

The catalog feed hides sold items, so "it vanished from the feed" is only a
*candidate*. We need a confirmation step. A seller's closet DOES list their
sold items, so we use a closet to obtain a genuinely-sold item id, then
compare its item page against a live item's page.

Rotating residential exits time out often -> everything goes through get().
"""

import json
import os
import re
import time

import requests

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
BASE = "https://www.vinted.it"
STATE_KEYS = ("closed", "hidden", "sold", "reserv", "visib", "active", "status")


def session():
    s = requests.Session()
    proxy = os.environ.get("PROXY_URL")
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9,en;q=0.8"})
    return s


def get(s, url, tries=4, **kw):
    """GET with retries — a rotating proxy hands us a new (sometimes dead) exit."""
    kw.setdefault("timeout", 45)
    for i in range(tries):
        try:
            return s.get(url, **kw)
        except requests.RequestException as e:
            print(f"    (retry {i + 1}/{tries} on {type(e).__name__})")
            time.sleep(2 * (i + 1))
    return None


def bootstrap(s):
    r = get(s, BASE + "/")
    print(f"[bootstrap] HTTP {r.status_code if r else 'FAIL'}")
    return r is not None and r.status_code == 200


def jget(s, url, **params):
    r = get(s, url, params=params or None, headers={"Accept": "application/json", "Referer": BASE + "/"})
    if r is None:
        print(f"  {url} -> no response")
        return None
    if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
        print(f"  {url} -> HTTP {r.status_code} ({r.headers.get('content-type', '?')[:30]})")
        return None
    return r.json()


def dump_state(tag, obj):
    hits = {k: obj[k] for k in obj if any(t in k for t in STATE_KEYS)}
    print(f"    {tag} state fields: {json.dumps(hits, ensure_ascii=False)[:400]}")
    return hits


def page_markers(s, url, tag):
    r = get(s, url)
    if r is None:
        print(f"  [{tag}] page FAILED")
        return
    html = r.text
    print(f"  [{tag}] HTTP {r.status_code}, {len(html)}B  {url}")
    for pat in ("Venduto", "venduto", "Non disponibile", "sold_at", "isClosed"):
        n = html.count(pat)
        if n:
            print(f"      text marker {pat!r} x{n}")
    for key in ("is_closed", "is_hidden", "is_reserved", "item_closing_action", "is_visible"):
        m = re.search(rf'\\?"{key}\\?"\s*:\s*(\\?"[^",}}]*\\?"|[^,}}\s]+)', html)
        print(f"      json {key} = {m.group(1) if m else '(absent)'}")


def main():
    s = session()
    if not bootstrap(s):
        raise SystemExit("bootstrap failed")

    print("\n=== 1. live item from the search feed ===")
    data = jget(s, BASE + "/api/v2/catalog/items", search_text="prada shoes",
                order="newest_first", per_page=20, page=1)
    if not data:
        raise SystemExit("search failed")
    items = data.get("items", [])
    live = items[0]
    uid = live["user"]["id"]
    print(f"  live item id={live['id']} user={uid} url={live['url']}")

    print("\n=== 2. seller closet — does it expose SOLD items? ===")
    closet = jget(s, f"{BASE}/api/v2/users/{uid}/items", page=1, per_page=20)
    sold_id = sold_url = None
    if closet:
        citems = closet.get("items", [])
        print(f"  closet has {len(citems)} items")
        if citems:
            print("  closet item keys:", ", ".join(sorted(citems[0].keys()))[:700])
            for ci in citems:
                hits = dump_state(f"id={ci.get('id')}", ci)
                truthy = any(
                    v is True or v == 1
                    for k, v in hits.items()
                    if any(t in k for t in ("closed", "sold", "reserv"))
                )
                if truthy and sold_id is None:
                    sold_id, sold_url = ci.get("id"), ci.get("url") or (BASE + ci.get("path", ""))
    print(f"  -> candidate SOLD item: {sold_id}")

    print("\n=== 3. item page markers: LIVE vs SOLD ===")
    page_markers(s, live["url"], "LIVE")
    if sold_url:
        page_markers(s, sold_url, "SOLD")
    else:
        print("  (no sold item found in that closet — will retry with another seller)")
        for cand in items[1:6]:
            c = jget(s, f"{BASE}/api/v2/users/{cand['user']['id']}/items", page=1, per_page=20)
            if not c:
                continue
            for ci in c.get("items", []):
                if ci.get("is_closed") or ci.get("is_sold"):
                    page_markers(s, ci.get("url") or BASE + ci.get("path", ""), "SOLD")
                    print("\n[probe] done")
                    return

    print("\n[probe] done")


if __name__ == "__main__":
    main()
