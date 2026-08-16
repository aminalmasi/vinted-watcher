"""Vestiaire sold-listing watcher.

The Vinted watcher had to *infer* sales: a listing vanished, and then a pile of
heuristics tried to decide whether that meant sold, hidden, reserved, deleted,
or the seller going on holiday. Most of that code existed to suppress false
positives.

None of it is needed here. Vestiaire answers `{"sold": true}` directly, so a
sweep is three honest steps:

    1. ask each brand for its sold listings inside the window
    2. diff against what we saw last time      -> these are new sales
    3. fetch each new sale once for its soldDate, then report it

The window filters on createdAt (when the item was LISTED), because that is the
only field the API will sort or range-filter on. So this sees sales of recently
listed items, and is blind to a listing from 2024 that sells today. That is a
deliberate, documented limitation, not an oversight — there is no sort-by-sale-
date to build on.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vestiaire.client import Vestiaire, url_of              # noqa: E402
from vestiaire.notify import format_sale, format_summary    # noqa: E402
from vintedwatch.notify import send                         # noqa: E402

log = logging.getLogger("vestiaire")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(REPO, "data", "vestiaire_state.json")

BRANDS = {
    "60":  "Prada",
    "117": "Miu Miu",
    "62":  "Maison Margiela",
    "236": "Christian Louboutin",
    "186": "Salvatore Ferragamo",
}
SHOES_WOMEN = "3"
WINDOW_DAYS = 30
# Entries only need to outlive the window they can still appear in; keeping
# them longer is how the Vinted state file reached 5.4 MB.
RETAIN_DAYS = 45
MAX_ALERTS = 25          # a burst goes to one summary line instead of 25 pings


def load() -> dict:
    try:
        with open(STATE) as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        st = {}
    st.setdefault("version", 1)
    st.setdefault("seen", {})
    st.setdefault("seeded", [])
    st.setdefault("runs", 0)
    return st


def save(st: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(st, fh, separators=(",", ":"))
    os.replace(tmp, STATE)


def prune(st: dict, now: float) -> int:
    """Drop what can no longer appear in the window, so state stays small."""
    floor = now - RETAIN_DAYS * 86400
    dead = [k for k, v in st["seen"].items() if (v.get("created_at") or now) < floor]
    for k in dead:
        del st["seen"][k]
    return len(dead)


def sweep(vc: Vestiaire, st: dict, dry: bool) -> tuple[list, dict]:
    now = time.time()
    gte = int(now - WINDOW_DAYS * 86400)
    new_sales, counts = [], {}

    for bid, name in BRANDS.items():
        items, complete = vc.sold_since(bid, SHOES_WOMEN, gte)
        counts[name] = len(items)
        first_time = bid not in st["seeded"]
        log.info("%-20s %3d sold in window%s%s", name, len(items),
                 "" if complete else " (TRUNCATED)",
                 "  [seeding]" if first_time else "")

        for it in items:
            pid = str(it.get("id"))
            if not pid or pid in st["seen"]:
                continue
            rec = {
                "brand": (it.get("brand") or {}).get("name") or name,
                "name": it.get("name"),
                "price": (it.get("price") or {}).get("cents", 0) / 100 or None,
                "size": (it.get("size") or {}).get("size"),
                "created_at": it.get("createdAt"),
                "url": url_of(it),
                "first_seen": now,
            }
            st["seen"][pid] = rec
            # On a brand's first sweep every sold listing is "new" only because
            # we have never looked. Record them, alert on none.
            if not first_time:
                new_sales.append((pid, rec))

        if first_time and not dry:
            st["seeded"].append(bid)

    return new_sales, counts


def enrich(vc: Vestiaire, st: dict, sales: list) -> None:
    """One call per sale for the exact soldDate. Only sales, so volume is tiny."""
    for pid, rec in sales[:MAX_ALERTS]:
        d = vc.product(pid)
        if not d:
            continue
        rec["sold_date"] = d.get("soldDate")
        rec["description"] = (d.get("description") or "")[:400]
        if d.get("price"):
            rec["price"] = d["price"].get("cents", 0) / 100 or rec.get("price")
        st["seen"][pid] = rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report, send nothing, persist nothing")
    ap.add_argument("--quiet", action="store_true", help="no per-sweep summary")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    started = time.time()
    st = load()
    vc = Vestiaire()

    sales, counts = sweep(vc, st, args.dry_run)
    log.info("%d new sales", len(sales))
    if sales:
        enrich(vc, st, sales)

    if not args.dry_run:
        for pid, rec in sales[:MAX_ALERTS]:
            send(format_sale(rec))
        if len(sales) > MAX_ALERTS:
            send(f"…e altre {len(sales)-MAX_ALERTS} vendite in questo sweep.")
        if not args.quiet and (sales or st["runs"] % 12 == 0):
            send(format_summary(counts, len(sales), time.time() - started))

    dropped = prune(st, time.time())
    st["runs"] += 1
    st["last_run"] = time.time()
    st["last_counts"] = counts
    if not args.dry_run:
        save(st)

    log.info("%d requests, %.1f MB, %d tracked, %d pruned, %.0f s%s",
             vc.requests, vc.bytes / 1024 / 1024, len(st["seen"]), dropped,
             time.time() - started, "  [THROTTLED]" if vc.throttled else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
