"""Harvest every sold listing we can still reach, with its metadata.

One brand per run, sliced by condition and month. Two reasons for the slicing:

  * CONDITION is not returned on a search hit, so the only way to label it is to
    ask for one condition at a time. It costs almost nothing — the items are
    partitioned, not duplicated, so the page count is the same.
  * MONTHS keep each query under the ~1000 pagination ceiling. Where a month is
    still too big (Gucci in 2026 runs ~1400) the window splits itself in half,
    recursively, until it fits. Without that, big cells silently truncate and
    the dataset would be quietly missing its most active periods.

Photo PATHS are captured but no images are downloaded. Paths do not expire — a
2018 listing's photo still resolves — so images can follow later at any pace,
for whatever subset turns out to be worth it.

Output is a JSONL artifact, deliberately not committed: 100k of someone else's
listings does not belong in a public repo.
"""

from __future__ import annotations

import calendar, json, logging, os, random, sys, time
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vestiaire.client import FIELDS, LOCALE, PAGE, SEARCH, UA   # noqa: E402
from vestiaire.run import BRANDS, SHOES_WOMEN                   # noqa: E402

BRAND = os.environ.get("VC_BRAND", "60")
# Same walk works for the live catalogue; only the `sold` filter differs.
SOLD = os.environ.get("VC_SOLD", "1") == "1"
FROM = os.environ.get("VC_FROM", "2023-01")
TO = os.environ.get("VC_TO", "")
CONDITIONS = ["1", "2", "3", "4", "5"]
SAFE = 900              # stay clear of the ~1000 pagination ceiling
OUT = f"vc_{'sold' if SOLD else 'live'}_{BRAND}.jsonl"

log = logging.getLogger("harvest")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9",
                  "Origin": "https://www.vestiairecollective.com",
                  "Referer": "https://www.vestiairecollective.com/",
                  "x-usecase": "catalog", "Content-Type": "application/json"})
STOP = False
REQS = 0


def call(cond, gte, lte, offset, limit=PAGE):
    global STOP, REQS
    if STOP:
        return None
    time.sleep(random.uniform(6, 10))
    body = {"pagination": {"offset": offset, "limit": limit}, "fields": FIELDS,
            "filters": {"brand.id": [BRAND], "categoryLvl0.id": [SHOES_WOMEN],
                        "condition.id": [cond], "sold": SOLD,
                        "createdAt": {"gte": gte, "lte": lte}},
            "locale": LOCALE, "sort": "recency"}
    for attempt in range(2):
        try:
            r = S.post(SEARCH, json=body, timeout=45)
        except requests.RequestException as exc:
            log.warning("  %s", type(exc).__name__)
            return None
        REQS += 1
        if r.status_code == 429:
            if attempt == 0:
                log.warning("  429 — waiting 120 s")
                time.sleep(120)
                continue
            STOP = True
            log.warning("  429 again — stopping")
            return None
        if r.status_code != 200:
            log.warning("  HTTP %d", r.status_code)
            return None
        return r.json()
    return None


def record(it, cond):
    pics = it.get("pictures") or []
    pic = pics[0] if pics else None
    if isinstance(pic, dict):
        pic = pic.get("path") or pic.get("url")
    link = it.get("link") or ""
    return {"id": it.get("id"), "brand": BRAND, "cond": cond,
            "name": it.get("name"), "price": (it.get("price") or {}).get("cents"),
            "created": it.get("createdAt"), "likes": it.get("likes"),
            "size": (it.get("size") or {}).get("size") if isinstance(it.get("size"), dict) else it.get("size"),
            # Subcategory is not a returned field, but the URL path carries it.
            "sub": link.split("/")[2] if link.count("/") > 2 else None,
            "pic": pic, "link": link,
            "pics": len(pics)}


def harvest(cond, gte, lte, fh, depth=0) -> int:
    """Pull one window, splitting it if it will not fit under the page cap."""
    j = call(cond, gte, lte, 0)
    if j is None:
        return 0
    total = int((j.get("paginationStats") or {}).get("totalHits") or 0)
    if total == 0:
        return 0
    if total > SAFE and (lte - gte) > 86400 and depth < 8:
        mid = gte + (lte - gte) // 2
        return harvest(cond, gte, mid, fh, depth + 1) + \
               harvest(cond, mid, lte, fh, depth + 1)

    n = 0
    items = j.get("items") or []
    while items:
        for it in items:
            fh.write(json.dumps(record(it, cond), ensure_ascii=False) + "\n")
            n += 1
        if n >= total or n >= SAFE:
            break
        nxt = call(cond, gte, lte, n)
        if nxt is None:
            break
        items = nxt.get("items") or []
    fh.flush()
    return n


def months(a: str, b: str):
    y, m = map(int, a.split("-"))
    if b:
        ey, em = map(int, b.split("-"))
    else:
        t = time.gmtime()
        ey, em = t.tm_year, t.tm_mon
    while (y, m) <= (ey, em):
        gte = calendar.timegm((y, m, 1, 0, 0, 0, 0, 0, 0))
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        yield f"{y}-{m:02d}", gte, calendar.timegm((ny, nm, 1, 0, 0, 0, 0, 0, 0))
        y, m = ny, nm


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log.info("harvesting %s %s (%s) from %s", "SOLD" if SOLD else "LIVE",
             BRANDS.get(BRAND, BRAND), BRAND, FROM)
    t0, total = time.time(), 0
    with open(OUT, "w", encoding="utf-8") as fh:
        for label, gte, lte in months(FROM, TO):
            if STOP:
                break
            got = sum(harvest(c, gte, lte, fh) for c in CONDITIONS)
            total += got
            if got:
                log.info("  %s  %5d  (running %6d, %d requests, %.1f h)",
                         label, got, total, REQS, (time.time() - t0) / 3600)
    log.info("\n%d listings -> %s (%.1f MB, %d requests)%s", total, OUT,
             os.path.getsize(OUT) / 1024 / 1024, REQS,
             "  STOPPED EARLY" if STOP else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
