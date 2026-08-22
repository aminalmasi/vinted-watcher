"""Longitudinal dataset: do likes predict whether (and how fast) a shoe sells?

The watcher only ever records items that ALREADY sold, which cannot answer the
question. If you look only at sales, every sale has some likes and you have no
idea whether unsold items had just as many. Measuring a relationship needs both
arms — the ones that sold and the ones that sat there.

So this samples LIVE listings for the tracked brands, follows the same items
over days, and records three kinds of event to data/vc_cohort.jsonl:

    new   first time we saw the listing, with its likes at that moment
    like  its like count changed (this is what builds a trajectory)
    sold  it turned up in the watcher's sold state, with days-to-sell
    gone  it vanished without a recorded sale — delisted, or a sale we missed

`gone` matters: treating those as "did not sell" would bias the result, so they
are marked as censored and left for the analysis to handle honestly.

Only CHANGES are appended, never a full daily dump, so the file grows with real
information rather than with repetition.

Runs on GitHub Actions — the search host 403s the university IP.
"""

from __future__ import annotations

import json
import logging
import os
import random
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vestiaire.client import (FIELDS, FLOOR_CENTS, LOCALE, PAGE,  # noqa: E402
                              SEARCH, UA)
from vestiaire.run import BRANDS, SHOES_WOMEN, STATE              # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data", "vc_cohort.jsonl")
CSTATE = os.path.join(REPO, "data", "vc_cohort_state.json")

WINDOW_DAYS = int(os.environ.get("VC_WINDOW_DAYS", "21"))
PAGES = int(os.environ.get("VC_PAGES", "6"))
GONE_AFTER = 2          # consecutive samples missing before we call it gone
DROP_AFTER_DAYS = 75    # stop tracking; its outcome can no longer be observed

log = logging.getLogger("cohort")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9",
                  "Origin": "https://www.vestiairecollective.com",
                  "Referer": "https://www.vestiairecollective.com/",
                  "x-usecase": "catalog", "Content-Type": "application/json"})
STOP = False


def sample(brand_id: str, gte: int) -> list:
    global STOP
    out = []
    for p in range(PAGES):
        if STOP:
            break
        time.sleep(random.uniform(6, 10))
        body = {"pagination": {"offset": p * PAGE, "limit": PAGE}, "fields": FIELDS,
                "filters": {"brand.id": [brand_id], "categoryLvl0.id": [SHOES_WOMEN],
                            "sold": False, "price": {"gte": FLOOR_CENTS},
                            "createdAt": {"gte": gte}},
                "locale": LOCALE, "sort": "recency"}
        try:
            r = S.post(SEARCH, json=body, timeout=45)
        except requests.RequestException as exc:
            log.warning("%s", type(exc).__name__)
            break
        if r.status_code == 429:
            STOP = True
            log.warning("429 — stopping the sample")
            break
        if r.status_code != 200:
            log.warning("HTTP %d", r.status_code)
            break
        items = (r.json().get("items") or [])
        if not items:
            break
        out.extend(items)
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    now = int(time.time())
    gte = now - WINDOW_DAYS * 86400

    try:
        cs = json.load(open(CSTATE))
    except (OSError, ValueError):
        cs = {}
    try:
        sold_state = json.load(open(STATE)).get("seen", {})
    except (OSError, ValueError):
        sold_state = {}

    events, seen_now = [], set()
    for bid, name in BRANDS.items():
        items = sample(bid, gte)
        log.info("%-20s sampled %4d live", name, len(items))
        for it in items:
            pid = str(it.get("id"))
            likes = it.get("likes")
            if not pid or likes is None:
                continue
            seen_now.add(pid)
            rec = cs.get(pid)
            if rec is None:
                cs[pid] = {"l": likes, "miss": 0, "c": it.get("createdAt"),
                           "out": None}
                events.append({"e": "new", "id": pid, "t": now, "b": name,
                               "p": round((it.get("price") or {}).get("cents", 0)/100, 2),
                               "l": likes, "c": it.get("createdAt")})
            else:
                rec["miss"] = 0
                if likes != rec.get("l"):
                    rec["l"] = likes
                    events.append({"e": "like", "id": pid, "t": now, "l": likes})

    # Outcomes. The watcher's own state is the source of truth for sales, so
    # this costs no extra requests.
    for pid, rec in cs.items():
        if rec.get("out"):
            continue
        s = sold_state.get(pid)
        if s and not s.get("seeded"):
            rec["out"] = "sold"
            events.append({"e": "sold", "id": pid, "t": now,
                           "l": s.get("likes"), "p": s.get("price"),
                           "d": s.get("days")})
        elif pid not in seen_now and not STOP:
            rec["miss"] = rec.get("miss", 0) + 1
            if rec["miss"] >= GONE_AFTER:
                rec["out"] = "gone"
                events.append({"e": "gone", "id": pid, "t": now, "l": rec.get("l")})

    # Retire items whose outcome can no longer be observed.
    dropped = [k for k, v in cs.items()
               if v.get("out") or (v.get("c") and v["c"] < now - DROP_AFTER_DAYS*86400)]
    for k in dropped:
        del cs[k]

    if events and not STOP:
        with open(DATA, "a") as fh:
            for e in events:
                fh.write(json.dumps(e, separators=(",", ":")) + "\n")
    json.dump(cs, open(CSTATE, "w"), separators=(",", ":"))

    kinds = {}
    for e in events:
        kinds[e["e"]] = kinds.get(e["e"], 0) + 1
    total = sum(1 for _ in open(DATA)) if os.path.exists(DATA) else 0
    log.info("events %s | tracking %d | retired %d | dataset %d rows",
             kinds or "none", len(cs), len(dropped), total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
