"""Does polling degrade over a LONG run, not just a 20-request block?

The short probe said 0.4s was fine, but 20 requests is far too few to see
throttling: a token bucket that allows a burst and then tightens would pass
that test and fail in production. Raising MAX_CHECK on that evidence would
risk the whole watcher.

So: one unbroken run of 400 item fetches at a fixed gap, reported in buckets of
50. Throttling shows up as a failure rate that CLIMBS across buckets. A flat
rate near zero means the gap is genuinely sustainable, which is what the
decision needs.
"""
from __future__ import annotations
import os, re, sys, time
import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
GAP = float(os.environ.get("GAP", "1.0"))
N = int(os.environ.get("N", "400"))
BUCKET = 50
BRANDS = ["gucci", "prada", "chanel", "dior", "hermes", "valentino",
          "golden goose", "saint laurent", "bottega veneta", "louboutin"]


def main() -> int:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
    s.get("https://www.vinted.it/", timeout=45)

    urls, seen = [], set()
    for b in BRANDS:
        for page in (1, 2, 3, 4, 5):
            r = s.get("https://www.vinted.it/catalog",
                      params={"search_text": f"{b} shoes", "page": page}, timeout=60)
            for i, sl in re.findall(r"/items/(\d{6,12})-([a-z0-9-]{3,60})", r.text):
                if i not in seen:
                    seen.add(i); urls.append(f"https://www.vinted.it/items/{i}-{sl}")
            time.sleep(1.0)
            if len(urls) >= N:
                break
        if len(urls) >= N:
            break
    urls = urls[:N]
    print(f"{len(urls)} urls, gap {GAP}s\n", flush=True)

    ok = bad = 0
    bo = bb = 0
    t0 = time.time()
    codes_seen = {}
    for k, u in enumerate(urls, 1):
        try:
            c = s.get(u, timeout=40).status_code
        except requests.RequestException:
            c = 0
        codes_seen[c] = codes_seen.get(c, 0) + 1
        if c == 200:
            ok += 1; bo += 1
        else:
            bad += 1; bb += 1
        if k % BUCKET == 0:
            el = time.time() - t0
            print(f"  after {k:>4}: bucket {bo}/{BUCKET} ok"
                  f"{'  <-- FAILURES' if bb else ''}"
                  f"   cumulative {ok}/{k}   {k/el:.2f} req/s", flush=True)
            bo = bb = 0
        time.sleep(GAP)

    el = time.time() - t0
    print(f"\n{ok}/{len(urls)} ok in {el/60:.1f} min = {len(urls)/el:.2f} req/s")
    print("status codes:", dict(sorted(codes_seen.items())))
    print(f"\nprojected reach in a 170-min job: {int(170*60*len(urls)/el)} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
