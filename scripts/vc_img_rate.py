"""What request rate will the image CDN actually tolerate?

All we know is the two ends: ~1 req/s FAILS (nine succeed, then a contiguous
block of refusals) and 6-10 s works. Everything between is guesswork, and
guessing wrong before committing ten parallel jobs would cost us the image host
entirely — far more expensive than fifteen minutes of measuring.

So: fetch real photo paths, then download in blocks at 1 s, 2 s and 4 s, and
report the success rate of each. A contiguous tail of failures means throttling;
scattered ones mean something else.
"""

from __future__ import annotations

import logging, os, random, sys, time
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vestiaire.client import FIELDS, LOCALE, SEARCH, UA        # noqa: E402
from vestiaire.run import SHOES_WOMEN                          # noqa: E402

IMG = "https://images.vestiairecollective.com/images/resized/w=400,q=75/produit/{p}"
BLOCK = int(os.environ.get("VC_BLOCK", "25"))

log = logging.getLogger("rate")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9",
                  "Origin": "https://www.vestiairecollective.com",
                  "Referer": "https://www.vestiairecollective.com/",
                  "x-usecase": "catalog", "Content-Type": "application/json"})


def paths(n=120):
    out = []
    for off in (0, 48, 96):
        time.sleep(random.uniform(6, 10))
        r = S.post(SEARCH, json={"pagination": {"offset": off, "limit": 48},
                                 "fields": FIELDS,
                                 "filters": {"categoryLvl0.id": [SHOES_WOMEN],
                                             "sold": True},
                                 "locale": LOCALE, "sort": "recency"}, timeout=45)
        if r.status_code != 200:
            break
        for it in r.json().get("items") or []:
            ps = it.get("pictures") or []
            p = ps[0] if ps else None
            if isinstance(p, dict):
                p = p.get("path") or p.get("url")
            if p:
                out.append(p)
        if len(out) >= n:
            break
    return out[:n]


def trial(ps, gap):
    ok = fail = 0
    codes, first_fail = [], None
    hdr = {"Referer": "https://www.vestiairecollective.com/",
           "Accept": "image/avif,image/webp,*/*", "User-Agent": UA}
    for i, p in enumerate(ps, 1):
        src = p if str(p).startswith("http") else IMG.format(p=p)
        try:
            r = requests.get(src, headers=hdr, timeout=30)
            code = r.status_code
        except requests.RequestException:
            code = 0
        codes.append(code)
        if code == 200:
            ok += 1
        else:
            fail += 1
            first_fail = first_fail or i
        time.sleep(gap)
    tail = all(c != 200 for c in codes[first_fail - 1:]) if first_fail else False
    log.info("  %4.1f s gap: %2d/%2d ok%s%s", gap, ok, len(ps),
             f", first failure at #{first_fail}" if first_fail else "",
             " (contiguous tail -> throttled)" if tail and fail > 1 else "")
    return ok, fail


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ps = paths()
    log.info("collected %d photo paths\n", len(ps))
    if len(ps) < 75:
        log.warning("not enough paths to test properly")
    i = 0
    for gap in (4.0, 2.0, 1.0):
        chunk = ps[i:i + BLOCK]
        i += BLOCK
        if not chunk:
            break
        ok, fail = trial(chunk, gap)
        if fail > len(chunk) // 3:
            log.info("  -> stopping; %.1f s is already too fast", gap)
            break
        time.sleep(30)      # let any bucket refill before the next trial
    return 0


if __name__ == "__main__":
    sys.exit(main())
