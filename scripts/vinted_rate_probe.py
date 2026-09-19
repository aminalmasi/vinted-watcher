"""How fast can item pages be polled before Vinted objects?

Rotation speed is currently set by a 3-6s gap I chose defensively and never
measured. At 400 checks per 3-hour cycle a full pass over 9,029 tracked
listings takes 2.8 days, which is far too slow if sale detection should be
timely.

Same method as the Vestiaire CDN probe: blocks of requests at decreasing gaps,
watching for a CONTIGUOUS tail of failures, which is what throttling looked
like there (a burst succeeds, then everything fails). Stops at the first sign
of it rather than hunting for the exact ceiling - the number needed is "what is
safe", not "what breaks".
"""
from __future__ import annotations
import json, os, random, re, sys, time
import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
BLOCK = int(os.environ.get("BLOCK", "20"))


def main() -> int:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
    s.get("https://www.vinted.it/", timeout=45)
    cat = s.get("https://www.vinted.it/catalog",
                params={"search_text": "prada shoes"}, timeout=60).text
    pairs = re.findall(r"/items/(\d{6,12})-([a-z0-9-]{3,60})", cat)
    urls = [f"https://www.vinted.it/items/{i}-{sl}" for i, sl in pairs]
    print(f"{len(urls)} item urls\n", flush=True)

    i = 0
    for gap in (3.0, 1.5, 0.8, 0.4):
        chunk = urls[i:i + BLOCK]; i += BLOCK
        if len(chunk) < 5:
            break
        ok = 0
        codes, first_fail = [], None
        t0 = time.time()
        for k, u in enumerate(chunk, 1):
            try:
                r = s.get(u, timeout=40)
                c = r.status_code
            except requests.RequestException:
                c = 0
            codes.append(c)
            if c == 200:
                ok += 1
            elif first_fail is None:
                first_fail = k
            time.sleep(gap)
        tail = first_fail and all(c != 200 for c in codes[first_fail - 1:])
        rate = len(chunk) / (time.time() - t0)
        print(f"  gap {gap:>4.1f}s: {ok}/{len(chunk)} ok, {rate:.2f} req/s"
              + (f", first failure #{first_fail}" if first_fail else "")
              + ("  CONTIGUOUS TAIL -> throttled" if tail and ok < len(chunk) - 1 else ""),
              flush=True)
        if ok < len(chunk) - 1:
            print(f"  -> {gap}s is already too fast; stopping", flush=True)
            break
        time.sleep(20)
    return 0


if __name__ == "__main__":
    sys.exit(main())
