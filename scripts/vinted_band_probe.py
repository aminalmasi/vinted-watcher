"""Do price bands actually partition the catalog, and lift the 960 cap?

Band slicing only expands coverage if two things hold, and an earlier spot
check suggested the first might NOT: asking for 200-2000 returned items priced
11-436, which would mean price_to is decorative.

Checks, per brand:
  1. do returned prices RESPECT the requested band?      (else slicing is fake)
  2. are bands DISJOINT in item ids?                     (else we re-read the same listings)
  3. does each band reach its own depth?                 (the point: more than 960 total)

Reports observed price ranges rather than asserting, because the failure mode
here is a filter that is silently ignored.
"""
from __future__ import annotations
import json, os, re, sys, time
import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
PAGES = int(os.environ.get("PAGES", "4"))
BANDS = {
    "Prada": [0, 45, 60, 80, 100, 150, 100000],
    "Hermes": [0, 120, 280, 402, 550, 750, 100000],
}


def page(s, brand, lo, hi, p):
    par = {"search_text": f"{brand} shoes", "page": p}
    if lo: par["price_from"] = lo
    if hi < 100000: par["price_to"] = hi
    par["currency"] = "EUR"
    try:
        r = s.get("https://www.vinted.it/catalog", params=par, timeout=60)
    except requests.RequestException:
        return {}
    if r.status_code != 200:
        print(f"      HTTP {r.status_code}", flush=True)
        return {}
    t = r.text
    ids = {i for i, _ in re.findall(r"/items/(\d{6,12})-([a-z0-9-]{3,60})", t)}
    pr = dict(re.findall(r'\\?"id\\?":(\d{6,12}),.{0,400}?\\?"amount\\?":\\?"([\d.]+)', t))
    return {i: float(pr[i]) for i in ids if i in pr}


def main() -> int:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
    s.get("https://www.vinted.it/", timeout=45)

    for brand, edges in BANDS.items():
        print(f"\n=== {brand} ===", flush=True)
        seen_by_band, allids = [], set()
        for i in range(len(edges) - 1):
            lo, hi = edges[i], edges[i + 1]
            got = {}
            for p in range(1, PAGES + 1):
                got.update(page(s, brand, lo, hi, p))
                time.sleep(1.5)
            if got:
                vals = sorted(got.values())
                inb = sum(1 for v in vals if lo <= v <= hi)
                print(f"  {lo:>6}-{hi:<6} {len(got):>4} items  "
                      f"price {vals[0]:>7.0f}..{vals[-1]:<7.0f}  "
                      f"in-band {100*inb/len(vals):>5.1f}%"
                      f"{'   <-- FILTER IGNORED' if inb/len(vals) < 0.8 else ''}",
                      flush=True)
            else:
                print(f"  {lo:>6}-{hi:<6}    0 items", flush=True)
            seen_by_band.append(set(got))
            allids |= set(got)
        tot = sum(len(x) for x in seen_by_band)
        print(f"  union {len(allids)} vs sum {tot} -> "
              f"{100*(tot-len(allids))/max(tot,1):.1f}% overlap between bands")
    return 0


if __name__ == "__main__":
    sys.exit(main())
