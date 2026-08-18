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

from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vestiaire.client import Vestiaire, url_of              # noqa: E402
from vestiaire.notify import format_digest                  # noqa: E402
from vintedwatch.notify import send                         # noqa: E402

log = logging.getLogger("vestiaire")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(REPO, "data", "vestiaire_state.json")

# The ten brands that actually move at this price point, ranked by shoes sold
# above EUR 150 in a 30-day window (see scripts/vc_brand_ranking.py). Together
# they are ~55% of all such sales; the five we started with were ~15%, and
# Ferragamo did not even reach the top 25 above the floor.
BRANDS = {
    "2":    "Gucci",
    "50":   "Chanel",
    "14":   "Hermès",
    "236":  "Christian Louboutin",
    "10":   "Dior",
    "60":   "Prada",
    "3119": "Saint Laurent",
    "88":   "Valentino Garavani",
    "809":  "Golden Goose",
    "115":  "Bottega Veneta",
}
SHOES_WOMEN = "3"
WINDOW_DAYS = 30
# Entries only need to outlive the window they can still appear in; keeping
# them longer is how the Vinted state file reached 5.4 MB.
RETAIN_DAYS = 45
REPORT_HOUR = 10         # Europe/Rome
REPORT_TZ = "Europe/Rome"


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
            created = it.get("createdAt")
            rec = {
                "brand": (it.get("brand") or {}).get("name") or name,
                "name": it.get("name"),
                "price": (it.get("price") or {}).get("cents", 0) / 100 or None,
                "created_at": created,
                "url": url_of(it),
                "first_seen": now,
                # Time-to-sell, measured from when we SAW it turn sold. That is
                # within one sweep of the truth, which is far finer than the
                # daily report resolves — so it is not worth an apiv2 call per
                # sale just to read the exact soldDate.
                "days": round((now - created) / 86400, 2) if created else None,
            }
            if first_time:
                # A brand's first sweep sees its whole existing sold backlog.
                # Those are not sales we witnessed, and `days` for them is just
                # age-at-seeding, so they must never reach a digest. Flagging
                # beats relying on timestamps: adding a brand later would
                # otherwise dump its backlog into the next morning's report,
                # which is exactly what happened on 2026-08-18.
                rec["seeded"] = True
            st["seen"][pid] = rec
            # On a brand's first sweep every sold listing is "new" only because
            # we have never looked. Record them, alert on none.
            if not first_time:
                new_sales.append((pid, rec))

        if first_time and not dry:
            st["seeded"].append(bid)

    return new_sales, counts


def due_for_report(st: dict, now: float) -> bool:
    """True once per day, on the first sweep at or after 10:00 Europe/Rome.

    Guarded by the date rather than the hour so a late or missed run still
    reports (a bit later) instead of skipping the day entirely.
    """
    local = datetime.fromtimestamp(now, ZoneInfo(REPORT_TZ))
    if local.hour < REPORT_HOUR:
        return False
    return st.get("last_report_date") != local.date().isoformat()


def since_last_report(st: dict, now: float) -> list:
    """Sales detected since the previous digest (fallback: last 24 h)."""
    floor = st.get("last_report_at") or (now - 24 * 3600)
    return [r for r in st["seen"].values()
            if not r.get("seeded") and (r.get("first_seen") or 0) > floor]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report, send nothing, persist nothing")
    ap.add_argument("--report", action="store_true",
                    help="send the digest now, whatever the clock says")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    started = time.time()
    st = load()
    vc = Vestiaire()

    sales, counts = sweep(vc, st, args.dry_run)
    log.info("%d new sales", len(sales))

    # Every brand always has hundreds of historical sold listings in the
    # window, so a total of zero cannot be a quiet day — it means the query
    # stopped matching, i.e. they changed the API or started blocking us.
    # Two in a row to ride out a single bad sweep.
    now = time.time()
    if sum(counts.values()) == 0:
        st["blind_runs"] = st.get("blind_runs", 0) + 1
        log.warning("blind sweep %d — every brand returned zero", st["blind_runs"])
        if st["blind_runs"] == 2 and not args.dry_run:
            send("⚠️ <b>Vestiaire</b> — due sweep di fila senza risultati per "
                 "nessun marchio. Probabile cambio API o blocco: le vendite "
                 "non vengono più rilevate.")
    else:
        st["blind_runs"] = 0

    report = args.report or due_for_report(st, now)
    if report:
        batch = since_last_report(st, now)
        hours = int((now - (st.get("last_report_at") or now - 86400)) / 3600)
        log.info("daily report: %d sales over %dh", len(batch), hours)
        if not args.dry_run:
            send(format_digest(batch, hours))
            st["last_report_date"] = datetime.fromtimestamp(
                now, ZoneInfo(REPORT_TZ)).date().isoformat()
            st["last_report_at"] = now

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
