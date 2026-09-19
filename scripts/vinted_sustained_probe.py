"""What polling rate does Vinted actually sustain from one IP?

A 20-request block at 0.4s passed 20/20, but 400 requests at a 1.0s gap came
back 21% HTTP 429 - and FLAT across every bucket, not climbing. That is a
steady-state rate limit: the short probe only passed because 20 requests fit
inside the burst allowance.

So the question is not "does it degrade" but "where is the ceiling". This walks
gaps from fast to slow and stops at the first one that comes back clean,
reporting GOOD requests per second - a gap that 429s a fifth of the time buys
nothing, because those checks have to be retried anyway.
"""
from __future__ import annotations
import os, re, sys, time
import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
GAPS = [float(x) for x in os.environ.get("GAPS", "2.0,3.0,4.5").split(",")]
N = int(os.environ.get("N", "150"))
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
    print(f"{len(urls)} urls, gaps {GAPS}\n", flush=True)

    for GAP in GAPS:
        print(f"\n--- gap {GAP}s ---", flush=True)
        ok = 0
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
                bb += 1
            if k % BUCKET == 0:
                el = time.time() - t0
                print(f"  after {k:>4}: bucket {bo}/{BUCKET} ok"
                      f"{'  <-- 429s' if bb else ''}"
                      f"   cumulative {ok}/{k}   {k/el:.2f} req/s", flush=True)
                bo = bb = 0
            time.sleep(GAP)
        el = time.time() - t0
        good = ok / len(urls)
        eff = ok / el
        print(f"  => {ok}/{len(urls)} ok ({100*good:.0f}%), "
              f"{eff:.3f} GOOD req/s, codes {dict(sorted(codes_seen.items()))}", flush=True)
        print(f"     usable checks in a 170-min job at this gap: {int(170*60*eff)}", flush=True)
        if good > 0.98:
            print("     clean - this gap is sustainable", flush=True)
            break
        print("     cooling down 120s", flush=True)
        time.sleep(120)
    return 0


if __name__ == "__main__":
    sys.exit(main())
