"""Can GitHub Actions read Vinted CATALOG pages without the proxy?

This decides the whole cost model. Actions minutes are free on a public repo,
but DataImpulse GB are metered and nearly spent. The old watcher hard-required
the proxy because Vinted blocked datacenter IPs on the API - but the API is now
gone for everyone, and Actions reached the homepage unproxied, so the block may
never have applied to page routes or may have changed.

Tests the three things a collector actually needs, unproxied:
  1. a catalog page, and whether listing ids and prices are in it
  2. an item page, and whether can_buy still distinguishes sold from live
  3. a CDN image, at the Vestiaire-matching 310x430 rather than f800
"""
from __future__ import annotations
import os, re, sys
import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def run(label, proxies):
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
    if proxies:
        s.proxies.update(proxies)
    print(f"\n=== {label} ===")
    try:
        h = s.get("https://www.vinted.it/", timeout=45)
        print(f"  homepage      {h.status_code}")
    except requests.RequestException as e:
        print(f"  homepage      ERR {type(e).__name__}"); return
    try:
        r = s.get("https://www.vinted.it/catalog",
                  params={"search_text": "prada shoes"}, timeout=60, stream=True)
        raw = len(r.raw.read(decode_content=False)); code = r.status_code; r.close()
        r2 = s.get("https://www.vinted.it/catalog",
                   params={"search_text": "prada shoes"}, timeout=60)
    except requests.RequestException as e:
        print(f"  catalog page  ERR {type(e).__name__}"); return
    ids = set(re.findall(r"/items/(\d{6,12})-", r2.text))
    prices = re.findall(r'\\?"price\\?":\{\\?"amount\\?":\\?"([\d.]+)', r2.text)
    imgs = re.findall(r"https://images\d*\.vinted\.net/[^\"\\\s]+/f800/[^\"\\\s]+", r2.text)
    print(f"  catalog page  {code}, wire {raw/1024:.0f} KB, "
          f"{len(ids)} listings, {len(prices)} prices, {len(imgs)} image urls")
    if not ids:
        print(f"  body: {r2.text[:120]!r}")
        return
    iid = sorted(ids)[0]
    try:
        it = s.get(f"https://www.vinted.it/items/{iid}", timeout=60)
        can = re.search(r'\\?"can_buy\\?":\s*(true|false)', it.text)
        av = re.search(r'"availability":"([^"]+)"', it.text)
        print(f"  item page     {it.status_code}, can_buy={can.group(1) if can else '-'}, "
              f"availability={av.group(1) if av else '-'}")
    except requests.RequestException as e:
        print(f"  item page     ERR {type(e).__name__}")
    if imgs:
        small = re.sub(r"/f800/", "/310x430/", imgs[0])
        for u, lab in ((imgs[0], "f800"), (small, "310x430")):
            try:
                ir = s.get(u, timeout=45)
                print(f"  image {lab:<8} {ir.status_code}, {len(ir.content)/1024:.0f} KB")
            except requests.RequestException as e:
                print(f"  image {lab:<8} ERR {type(e).__name__}")


run("Actions, NO proxy", None)
px = os.environ.get("PROXY_URL")
if px:
    run("Actions, VIA proxy", {"http": px, "https": px})
