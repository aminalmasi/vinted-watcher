"""Precompute what each kind of shoe is worth on Vestiaire.

Valuing a Vinted listing live costs ~20 requests and three minutes, which is
fine for one URL and impossible for a feed. So the value side is precomputed
into a table keyed by (brand, subcategory, condition) and the scanner just does
a dictionary lookup.

Full granularity: 10 brands x 10 subcategories x 5 conditions = 500 cells. Most
are empty and cost one request to establish that; populated ones take two. At
the 6-10 s pace that is roughly 1.5-2 hours, so this is a WEEKLY job and it is
RESUMABLE — a timeout or a 429 loses nothing, the next run skips finished cells.

Medians come from SOLD listings only. Live prices are asking prices, and asking
prices include everything that never sells.
"""

from __future__ import annotations

import json, logging, os, random, statistics as st, sys, time
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vestiaire.client import FIELDS, LOCALE, PAGE, SEARCH, UA   # noqa: E402
from vestiaire.run import BRANDS                                # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "data", "vc_price_table.json")

# categoryLvl1 ids, from the facet dump. The three vestigial ones (26, 25, 27 —
# a few dozen items site-wide) are skipped: they cost 50 requests to confirm
# they are empty.
SUBCATS = {
    "510": "Scarpe con tacco", "507": "Sandali", "64": "Scarpe da ginnastica",
    "62": "Stivali", "505": "Mocassini", "511": "Stivaletti",
    "506": "Ballerine", "508": "Zoccoli", "1051": "Espadrillas",
    "509": "Scarpe derby",
}
CONDITIONS = {"1": "Mai indossato, con etichetta", "2": "Mai indossato",
              "3": "Ottimo stato", "4": "Buono stato", "5": "Corretto"}
WINDOW_DAYS = int(os.environ.get("VC_WINDOW_DAYS", "365"))
MAX_PAGES = 2
MIN_N = 5          # below this a median is noise, so the cell is marked thin

log = logging.getLogger("table")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9",
                  "Origin": "https://www.vestiairecollective.com",
                  "Referer": "https://www.vestiairecollective.com/",
                  "x-usecase": "catalog", "Content-Type": "application/json"})
STOP = False


def query(brand, sub, cond, offset):
    global STOP
    if STOP:
        return [], 0
    time.sleep(random.uniform(6, 10))
    body = {"pagination": {"offset": offset, "limit": PAGE}, "fields": FIELDS,
            "filters": {"brand.id": [brand], "categoryLvl1.id": [sub],
                        "condition.id": [cond], "sold": True,
                        "createdAt": {"gte": int(time.time() - WINDOW_DAYS * 86400)}},
            "locale": LOCALE, "sort": "recency"}
    try:
        r = S.post(SEARCH, json=body, timeout=45)
    except requests.RequestException as exc:
        log.warning("  %s", type(exc).__name__)
        return [], 0
    if r.status_code == 429:
        STOP = True
        log.warning("  429 — stopping; rerun to resume")
        return [], 0
    if r.status_code != 200:
        log.warning("  HTTP %d", r.status_code)
        return [], 0
    j = r.json()
    return (j.get("items") or []), int((j.get("paginationStats") or {}).get("totalHits") or 0)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        table = json.load(open(OUT))
    except (OSError, ValueError):
        table = {"built": None, "cells": {}}
    cells = table["cells"]

    todo = [(b, s, c) for b in BRANDS for s in SUBCATS for c in CONDITIONS
            if f"{b}/{s}/{c}" not in cells]
    log.info("%d cells total, %d already done, %d to do",
             len(BRANDS) * len(SUBCATS) * len(CONDITIONS), len(cells), len(todo))

    done = 0
    for brand, sub, cond in todo:
        if STOP:
            break
        key = f"{brand}/{sub}/{cond}"
        items, total = query(brand, sub, cond, 0)
        if STOP:
            break
        prices = [(i.get("price") or {}).get("cents", 0) / 100 for i in items
                  if (i.get("price") or {}).get("cents")]
        if total > PAGE and len(prices) < 96:
            more, _ = query(brand, sub, cond, PAGE)
            prices += [(i.get("price") or {}).get("cents", 0) / 100 for i in more
                       if (i.get("price") or {}).get("cents")]
        if prices:
            ps = sorted(prices)
            qs = st.quantiles(ps, n=4) if len(ps) >= 4 else [ps[0], st.median(ps), ps[-1]]
            cells[key] = {"n": len(ps), "total": total, "median": round(st.median(ps), 2),
                          "q1": round(qs[0], 2), "q3": round(qs[2], 2),
                          "thin": len(ps) < MIN_N}
        else:
            cells[key] = {"n": 0, "total": total, "thin": True}
        done += 1
        if done % 10 == 0 or cells[key]["n"]:
            log.info("  %-22s %-22s %-28s n=%-4d median=%s",
                     BRANDS[brand], SUBCATS[sub], CONDITIONS[cond],
                     cells[key]["n"], cells[key].get("median", "-"))
        if done % 20 == 0:            # checkpoint so a timeout costs nothing
            table["built"] = int(time.time())
            json.dump(table, open(OUT, "w"), indent=0)

    table["built"] = int(time.time())
    table["brands"], table["subcats"], table["conditions"] = BRANDS, SUBCATS, CONDITIONS
    json.dump(table, open(OUT, "w"), indent=0)
    filled = sum(1 for c in cells.values() if c["n"] >= MIN_N)
    log.info("\n%d cells stored, %d usable (n>=%d)%s",
             len(cells), filled, MIN_N, " — STOPPED EARLY, rerun to resume" if STOP else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
