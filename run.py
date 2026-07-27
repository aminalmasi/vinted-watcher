#!/usr/bin/env python3
"""Vinted IT watcher — reports SOLD listings to Telegram, nothing else.

Per the brief: new listings are tracked *silently*; only a sale produces a
message.

The tricky part is that "missing from the feed" does not mean "sold". The
catalog is ordered newest_first and we only read a few pages, so every listing
eventually drops out of our window simply by ageing. We therefore compare each
missing listing against the feed's age floor:

  * missing but NEWER than the floor -> it should have been there; it is gone
    for real -> confirm against its item page (sold / removed / still live).
  * missing and OLDER than the floor -> it merely aged out of our window; keep
    it, but only re-check it every RECHECK_HOURS so the proxy bill stays flat.

Confirmations are capped per run so a bad day cannot blow the data budget.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from vintedwatch import notify, state as state_mod
from vintedwatch.client import VintedClient, parse_item

log = logging.getLogger("vintedwatch")

SEARCH = {
    "search_text": "prada shoes",
    "order": "newest_first",
    "per_page": 24,
}
# Small pages on purpose: a 96-item page is ~426 KB, which slow residential
# exits regularly fail to deliver inside the timeout. 24 items is ~110 KB.
MAX_PAGES = 8               # ~192 newest listings per poll
SEED_DAYS = 5               # first run: only remember the last 5 days
RECHECK_HOURS = 6           # how often to re-check a listing that aged out
MAX_CHECKS_PER_RUN = 15     # hard cap on item-page fetches (traffic control)
MAX_TRACK_DAYS = 30         # give up on a listing that never sells
DAY = 86400


def fetch_feed(client: VintedClient) -> tuple[dict[int, dict], int | None]:
    """Read the newest pages of the search. Returns {id: item}, age floor."""
    items: dict[int, dict] = {}
    complete = True
    for page in range(1, MAX_PAGES + 1):
        raw = client.search(SEARCH, page=page)
        if raw is None:
            # A failed page truncates our view of the feed. We cannot tell an
            # aged-out listing from a vanished one, so distrust the floor.
            log.warning("page %d failed — age floor will not be trusted", page)
            complete = False
            break
        if not raw:
            break  # genuinely the end of the results
        for r in raw:
            parsed = parse_item(r)
            if parsed["id"]:
                items[parsed["id"]] = parsed
        if len(raw) < SEARCH["per_page"]:
            break
        time.sleep(1.5)  # be gentle; this is someone's residential connection

    stamps = [i["photo_ts"] for i in items.values() if i.get("photo_ts")]
    # Only trust the floor if we actually paged to the bottom of our window;
    # a too-high floor would flag healthy listings as vanished.
    floor = min(stamps) if stamps and complete else None
    log.info("feed: %d listings, age floor=%s%s",
             len(items), floor, "" if complete else " (feed truncated)")
    return items, floor


def pick_checks(tracked: dict, live_ids: set, floor: int | None, now: float) -> list[dict]:
    """Choose which vanished listings to confirm, cheapest-signal first."""
    urgent, stale = [], []
    for key, rec in tracked.items():
        if int(key) in live_ids:
            continue
        last_check = rec.get("last_check", 0)
        photo_ts = rec.get("photo_ts") or 0
        vanished_early = floor is not None and photo_ts >= floor
        if vanished_early:
            # Disappeared while still inside the feed window -> check now.
            urgent.append((last_check, rec))
        elif now - last_check > RECHECK_HOURS * 3600:
            stale.append((last_check, rec))
    urgent.sort(key=lambda t: t[0])
    stale.sort(key=lambda t: t[0])
    chosen = [r for _, r in urgent] + [r for _, r in stale]
    if len(chosen) > MAX_CHECKS_PER_RUN:
        log.warning(
            "%d listings need confirming, checking the %d oldest this run",
            len(chosen), MAX_CHECKS_PER_RUN,
        )
    return chosen[:MAX_CHECKS_PER_RUN]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="never send Telegram messages")
    ap.add_argument("--test-telegram", action="store_true", help="send a ping and exit")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )

    if args.test_telegram:
        ok = notify.send("✅ <b>vinted_ads_bot</b> è collegato. Ti scriverò solo quando un annuncio viene <b>venduto</b>.")
        print("telegram ok" if ok else "telegram NOT configured/failed")
        return 0 if ok else 1

    st = state_mod.load()
    tracked = st["items"]
    first_run = not tracked
    now = time.time()

    client = VintedClient(token_cache=st.get("token"))
    live, floor = fetch_feed(client)
    if not live:
        log.error("empty feed — aborting without touching state")
        return 1

    # --- absorb the feed ------------------------------------------------
    seeded = skipped = 0
    for item_id, item in live.items():
        key = str(item_id)
        if key in tracked:
            tracked[key].update(item)
            tracked[key]["last_seen"] = now
            tracked[key]["missing_since"] = None
            continue
        if first_run and item.get("photo_ts") and now - item["photo_ts"] > SEED_DAYS * DAY:
            skipped += 1
            continue  # brief: seed only the last 5 days
        tracked[key] = dict(item, first_seen=now, last_seen=now,
                            missing_since=None, last_check=0)
        seeded += 1
    if first_run:
        log.info("seeded %d listings from the last %d days (%d older skipped)",
                 seeded, SEED_DAYS, skipped)
    elif seeded:
        log.info("%d new listings now tracked (silently — no alert)", seeded)

    for key, rec in tracked.items():
        if int(key) not in live and not rec.get("missing_since"):
            rec["missing_since"] = now

    # --- confirm the ones that vanished ---------------------------------
    sold_msgs, drop = [], []
    for n, rec in enumerate(pick_checks(tracked, set(live), floor, now)):
        if n:
            time.sleep(4)  # pace item-page hits; Vinted throttles bursts with 403s
        key = str(rec["id"])
        verdict = client.check_sold(rec["id"], rec.get("url"))
        rec["last_check"] = now
        log.info("check %s -> %s (%s)", key, verdict, rec.get("title", "")[:50])

        if verdict == "sold":
            listed = rec.get("first_seen") or rec.get("photo_ts")
            hours = (now - listed) / 3600 if listed else None
            sold_msgs.append((rec, hours))
            st["sold"][key] = {
                "reported_at": now,
                "title": rec.get("title"),
                "price": rec.get("price"),
                "currency": rec.get("currency"),
                "url": rec.get("url"),
                "hours_listed": round(hours, 1) if hours else None,
            }
            drop.append(key)
        elif verdict == "removed":
            drop.append(key)  # seller pulled it — not a sale, stay quiet
        elif verdict == "live":
            rec["missing_since"] = None

    for key in drop:
        tracked.pop(key, None)

    # --- retire listings that will never resolve -------------------------
    for key, rec in list(tracked.items()):
        if now - (rec.get("first_seen") or now) > MAX_TRACK_DAYS * DAY:
            tracked.pop(key, None)

    # --- notify -----------------------------------------------------------
    for rec, hours in sold_msgs:
        text = notify.format_sold(rec, hours)
        if args.dry_run:
            log.info("[dry-run] would send:\n%s", text)
        else:
            notify.send(text)
    log.info("SOLD this run: %d", len(sold_msgs))

    st["token"] = client.token_cache
    st["last_run"] = now
    state_mod.save(st)
    log.info("traffic this run: %.0f KB uncompressed (metered ~%.0f KB gzipped)",
             client.bytes_uncompressed / 1024, client.bytes_uncompressed / 1024 / 7)
    return 0


if __name__ == "__main__":
    sys.exit(main())
