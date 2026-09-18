"""Is Vinted's v2 API gone, or just refusing the university IP?

From the cluster every /api/v2/ endpoint now returns 404 with an HTML error
page while the homepage and item pages still load normally. A 404 rather than a
403 is exactly how a site fobs off an IP without admitting to it, so the
question is whether the same calls work from elsewhere.

Three legs, so the answer is attributable:
  A  GitHub Actions, no proxy   - datacenter IP, historically blocked by Vinted
  B  GitHub Actions, via proxy  - the residential Italian exit the old watcher used
  C  item page via proxy        - confirms the proxy session itself is healthy

Never logs the proxy URL: it carries credentials and this runs in a public repo.
"""
from __future__ import annotations
import os, sys
import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
API = "https://www.vinted.it/api/v2/catalog/items"


def leg(label, proxies):
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
    if proxies:
        s.proxies.update(proxies)
    try:
        h = s.get("https://www.vinted.it/", timeout=45)
    except requests.RequestException as e:
        print(f"{label}: homepage ERR {type(e).__name__}")
        return None
    tok = s.cookies.get("access_token_web")
    print(f"{label}\n  homepage {h.status_code}, token {'yes' if tok else 'NO'}")
    try:
        r = s.get(API, params={"search_text": "prada shoes", "per_page": 5, "page": 1},
                  headers={"Accept": "application/json",
                           "Referer": "https://www.vinted.it/catalog"}, timeout=45)
    except requests.RequestException as e:
        print(f"  api ERR {type(e).__name__}")
        return s
    ct = r.headers.get("content-type", "")
    print(f"  api {r.status_code}  content-type {ct.split(';')[0]}")
    if r.status_code == 200 and "json" in ct:
        items = r.json().get("items", [])
        print(f"  -> {len(items)} items; first: {str(items[0].get('title'))[:40] if items else '-'}")
        if items:
            it = items[0]
            print(f"     brand={it.get('brand_title')} size={it.get('size_title')} "
                  f"status={it.get('status')} price={(it.get('price') or {}).get('amount')}")
    else:
        print(f"  body: {r.text[:100]!r}")
    return s


leg("A. Actions, NO proxy", None)
px = os.environ.get("PROXY_URL")
if px:
    s = leg("B. Actions, VIA proxy", {"http": px, "https": px})
    if s is not None:
        try:
            r = s.get("https://www.vinted.it/items/9613936440-margiela-tabi", timeout=45)
            print(f"C. item page via proxy: {r.status_code}, {len(r.text):,} bytes")
        except requests.RequestException as e:
            print(f"C. item page via proxy: ERR {type(e).__name__}")
else:
    print("B/C skipped: no PROXY_URL")
