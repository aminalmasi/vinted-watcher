"""What rate does apiv2 tolerate from the cluster?

The live-photo corpus needs ~86,630 calls to apiv2/products/{id} to collect the
four photo paths search does not return. This endpoint is also what `vc compare`
depends on, so getting it blocked would cost a working tool as well as the
corpus — worth measuring rather than assuming.

Same method as the CDN probe: blocks at decreasing gaps, watching for a
CONTIGUOUS tail of failures, which is what throttling looked like there.
"""
from __future__ import annotations
import logging, sys, time
import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
Q = "isoCountry=IT&x-siteid=12&x-language=it&x-currency=EUR"
log = logging.getLogger("rate")


S = requests.Session()
S.headers.update({"User-Agent": UA})


def fetch(pid):
    """requests, not urllib: this conda env's urllib cannot verify Vestiaire's
    certificate, and the first version of this probe reported those local TLS
    failures as 0/25 server refusals — a local misconfiguration dressed up as a
    block. Any genuine transport error is surfaced, never swallowed as a code."""
    try:
        r = S.get(f"https://apiv2.vestiairecollective.com/products/{pid}?{Q}", timeout=25)
    except requests.RequestException as exc:
        log.warning("    transport error: %s", type(exc).__name__)
        return 0, 0
    if r.status_code != 200:
        return r.status_code, 0
    d = (r.json() or {}).get("data") or {}
    return 200, len(d.get("pictures") or [])


def trial(ids, gap):
    ok = pics = 0
    codes, first_fail = [], None
    for i, pid in enumerate(ids, 1):
        c, np = fetch(pid)
        codes.append(c)
        if c == 200:
            ok += 1; pics += np
        elif first_fail is None:
            first_fail = i
        time.sleep(gap)
    tail = first_fail and all(c != 200 for c in codes[first_fail - 1:])
    log.info("  %4.1f s: %2d/%2d ok, median photos %s%s%s", gap, ok, len(ids),
             round(pics / ok, 1) if ok else "-",
             f", first failure #{first_fail}" if first_fail else "",
             "  (contiguous tail -> throttled)" if tail and len(codes) - first_fail > 1 else "")
    return ok, len(ids) - ok


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ids = open("/tmp/live_ids.txt").read().split("LIVE_IDS ")[1].strip().split(",")
    log.info("%d live ids\n", len(ids))
    i = 0
    for gap in (2.0, 1.0, 0.5):
        chunk = ids[i:i + 25]; i += 25
        if len(chunk) < 5:
            break
        ok, bad = trial(chunk, gap)
        if bad > len(chunk) // 3:
            log.info("  -> %.1f s is too fast; stopping", gap)
            break
        time.sleep(20)
    return 0


if __name__ == "__main__":
    sys.exit(main())
