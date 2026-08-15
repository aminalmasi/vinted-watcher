"""Can an Italian residential exit reach the two Vestiaire hosts our IP cannot?

From the cluster (university IP) the split is sharp and already measured:

    apiv2.vestiairecollective.com/products/{id}   200  — full product JSON
    apiv2.vestiairecollective.com/brands          200  — 17.5k brands
    assets.vestiairecollective.com                200  — their JS bundles
    search.vestiairecollective.com/v1/product/search   403  Cloudflare WAF
    images.vestiairecollective.com/...                403  Cloudflare WAF
    www.vestiairecollective.com/...                   403  managed challenge

Product *state* is therefore already solved without a proxy — `sold`, `soldDate`
and `creationDate` come straight off apiv2. What is missing is DISCOVERY: the
brand-filtered search that turns "Prada shoes" into a list of product ids.

This probe answers exactly one question: does a residential Italian exit see
those hosts differently than we do? Nothing is built on the answer yet.

Never log the proxy URL — it carries credentials, and this runs in CI.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time

import requests

SEARCH = "https://search.vestiairecollective.com/v1/product/search"
APIV2 = "https://apiv2.vestiairecollective.com"
IMAGE = ("https://images.vestiairecollective.com/images/resized/w=1024,q=75"
         "/produit/55060547-1_2.jpg")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Straight out of their own bundle (fenx v4.102.2).
FIELDS = ["name", "description", "brand", "model", "country", "price", "discount",
          "link", "sold", "likes", "editorPicks", "shouldBeGone", "seller",
          "directShipping", "local", "pictures", "colors", "size", "stock",
          "universeId", "createdAt"]
LOCALE = {"country": "IT", "currency": "EUR", "language": "it", "sizeType": "women"}
BRANDS = {"Prada": "60", "Miu Miu": "117", "Maison Martin Margiela": "62",
          "Christian Louboutin": "236", "Salvatore Ferragamo": "186"}

BLOCK_MARKS = ("just a moment", "attention required", "cf-error", "cloudflare")


def verdict(r: requests.Response) -> str:
    """403 alone is ambiguous; say which wall it is."""
    body = (r.text or "")[:4000].lower()
    if r.status_code == 200:
        return "OK"
    if "just a moment" in body:
        return "managed JS challenge"
    if "attention required" in body or "cf-error" in body:
        return "Cloudflare WAF"
    if any(m in body for m in BLOCK_MARKS):
        return "cloudflare (other)"
    return f"HTTP {r.status_code}"


def run(label: str, proxies: dict | None) -> dict:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
    if proxies:
        s.proxies.update(proxies)
    out: dict = {"label": label}
    print(f"\n{'='*62}\n{label}\n{'='*62}")

    # 0. Control: a host we know answers our own IP. Proves the exit works at
    #    all before we read anything into a 403 from the blocked hosts.
    try:
        r = s.get(f"{APIV2}/products/55060547",
                  params={"isoCountry": "IT", "x-siteid": "12",
                          "x-language": "it", "x-currency": "EUR"}, timeout=45)
        d = (r.json().get("data") or {}) if r.status_code == 200 else {}
        out["apiv2"] = r.status_code
        print(f"apiv2 /products/{{id}}   {r.status_code}  {verdict(r)}"
              + (f"  sold={d.get('sold')} created={d.get('creationDate')}" if d else ""))
    except requests.RequestException as exc:
        out["apiv2"] = f"ERR {type(exc).__name__}"
        print(f"apiv2 /products/{{id}}   ERR {type(exc).__name__}")
    time.sleep(random.uniform(2, 4))

    # 1. The question that matters: brand-filtered discovery.
    body = {
        "pagination": {"offset": 0, "limit": 5},
        "fields": FIELDS,
        "filters": {"brand.id": [BRANDS["Prada"]]},
        "locale": LOCALE,
        "sort": "recency",
    }
    try:
        r = s.post(SEARCH, json=body, timeout=45,
                   headers={"Content-Type": "application/json",
                            "Origin": "https://www.vestiairecollective.com",
                            "Referer": "https://www.vestiairecollective.com/",
                            "x-usecase": "catalog"})
        out["search"] = r.status_code
        print(f"search /v1/product/search  {r.status_code}  {verdict(r)}")
        if r.status_code == 200:
            j = r.json()
            items = j.get("items") or j.get("data") or []
            print(f"    -> {len(items)} items, total={j.get('total') or j.get('meta')}")
            for it in items[:3]:
                print(f"       id={it.get('id')} sold={it.get('sold')} "
                      f"created={it.get('createdAt')} "
                      f"{str(it.get('name'))[:40]}")
            out["sample"] = items[:3]
        else:
            print(f"    body[:160]: {r.text[:160]!r}")
    except requests.RequestException as exc:
        out["search"] = f"ERR {type(exc).__name__}"
        print(f"search /v1/product/search  ERR {type(exc).__name__}")
    time.sleep(random.uniform(2, 4))

    # 2. Photos — needed only if we ever want a Vestiaire archive like Vinted's.
    try:
        r = s.get(IMAGE, timeout=45,
                  headers={"Referer": "https://www.vestiairecollective.com/",
                           "Accept": "image/avif,image/webp,*/*"})
        ok = r.status_code == 200 and r.headers.get("content-type", "").startswith("image")
        out["images"] = r.status_code
        print(f"images (1 photo)          {r.status_code}  "
              f"{'image, %d KB' % (len(r.content)//1024) if ok else verdict(r)}")
    except requests.RequestException as exc:
        out["images"] = f"ERR {type(exc).__name__}"
        print(f"images (1 photo)          ERR {type(exc).__name__}")

    return out


def main() -> int:
    proxy = os.environ.get("PROXY_URL")
    results = [run("A. GitHub Actions datacenter IP (no proxy)", None)]
    if proxy:
        time.sleep(3)
        results.append(run("B. DataImpulse residential exit, Italy",
                           {"http": proxy, "https": proxy}))
    else:
        print("\n(no PROXY_URL set — skipped the residential leg)")

    print(f"\n{'='*62}\nSUMMARY\n{'='*62}")
    for r in results:
        print(f"  {r['label']}")
        for k in ("apiv2", "search", "images"):
            print(f"      {k:8} {r.get(k)}")
    unlocked = any(r.get("search") == 200 for r in results)
    print(f"\n  discovery via search endpoint: "
          f"{'REACHABLE' if unlocked else 'still blocked'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
