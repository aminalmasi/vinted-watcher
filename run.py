#!/usr/bin/env python3
"""Vinted IT watcher — reports SOLD listings to Telegram, nothing else.

Per the brief: new listings are tracked *silently*; only a sale produces a
message.

The tricky part is that "missing from the feed" does not mean "sold". Vinted's
search churns between polls, so a listing can drop out and come back. Only a
listing absent from several consecutive polls is really gone, and only then do
we spend a page fetch to find out whether it sold, was removed, or is fine.

Confirmations are capped per run: each item page costs ~340 KB of metered
residential proxy traffic, which is the real budget here.
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
MISSING_RUNS = 3            # consecutive absences before a listing is suspicious
MAX_CHECKS_PER_RUN = 6      # hard cap on item-page fetches (~340 KB each, metered)
MAX_TRACK_DAYS = 30         # give up on a listing that never sells
UNKNOWN_GIVE_UP = 3         # consecutive failed confirmations before backing off
DAY = 86400


def fetch_feed(client: VintedClient) -> tuple[dict[int, dict], int | None, bool]:
    """Read the newest pages of the search. Returns {id: item}, age floor, complete."""
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
    return items, floor, complete


def pick_checks(tracked: dict, live_ids: set) -> list[dict]:
    """Choose which vanished listings are worth spending a page fetch on.

    An item's `photo_ts` looked like a way to tell "vanished" from "aged out of
    our window", but it is not: photos get re-uploaded and listings bumped, so
    the feed's oldest photo is weeks older than its newest while both sit in the
    same 190 results. The ordering is simply not by photo date.

    What does hold is persistence. Vinted's search churns a little every poll —
    the same query returns 187, then 190, then 188 listings, with membership
    wobbling at the edges. A listing absent from several consecutive polls is
    genuinely gone; one absent from a single poll is usually just churn. So we
    only pay for a confirmation after MISSING_RUNS consecutive absences.
    """
    candidates = [
        rec for key, rec in tracked.items()
        if int(key) not in live_ids and rec.get("missing_runs", 0) >= MISSING_RUNS
    ]
    # Longest-unresolved first, so nothing starves behind the per-run cap.
    candidates.sort(key=lambda r: (r.get("last_check", 0), -r.get("missing_runs", 0)))
    if len(candidates) > MAX_CHECKS_PER_RUN:
        log.info("%d listings await confirmation, checking %d this run (rest carry over)",
                 len(candidates), MAX_CHECKS_PER_RUN)
    return candidates[:MAX_CHECKS_PER_RUN]


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
    live, floor, complete = fetch_feed(client)
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
            tracked[key]["missing_runs"] = 0
            continue
        if first_run and item.get("photo_ts") and now - item["photo_ts"] > SEED_DAYS * DAY:
            skipped += 1
            continue  # brief: seed only the last 5 days
        tracked[key] = dict(item, first_seen=now, last_seen=now,
                            missing_runs=0, last_check=0)
        seeded += 1
    if first_run:
        log.info("seeded %d listings from the last %d days (%d older skipped)",
                 seeded, SEED_DAYS, skipped)
    elif seeded:
        log.info("%d new listings now tracked (silently — no alert)", seeded)

    if complete:
        for key, rec in tracked.items():
            if int(key) not in live:
                rec["missing_runs"] = rec.get("missing_runs", 0) + 1
        absent = sum(1 for r in tracked.values() if r.get("missing_runs", 0))
        log.info("%d tracked listings absent from this poll", absent)

    # --- confirm the ones that vanished ---------------------------------
    sold_msgs, drop = [], []
    unknowns = 0
    if not complete:
        # Half the feed is missing, so "absent from the feed" tells us nothing.
        # Confirming now would spend metered traffic re-checking listings that
        # never went anywhere. Skip; the next run sees the full window.
        checks = []
        log.warning("feed incomplete — skipping confirmations this run")
    else:
        checks = pick_checks(tracked, set(live))
    if checks:
        # Vinted only serves item pages to a session that has just loaded the
        # site; a stale anon token earns a 403. One homepage hit up front makes
        # every confirmation below work.
        try:
            client.bootstrap()
        except RuntimeError as exc:
            # Without a live session every item page 403s, so do not even try.
            log.warning("could not refresh the session (%s) — skipping confirmations", exc)
            checks = []
    for n, rec in enumerate(checks):
        if unknowns >= UNKNOWN_GIVE_UP:
            # Vinted is refusing item pages from our exits right now. Further
            # attempts only burn metered traffic; the listings stay tracked and
            # get re-checked next run.
            log.warning("%d confirmations failed in a row — skipping the rest this run", unknowns)
            break
        if n:
            time.sleep(4)  # pace item-page hits; Vinted throttles bursts with 403s
        key = str(rec["id"])
        verdict = client.check_sold(rec["id"], rec.get("url"))
        unknowns = unknowns + 1 if verdict == "unknown" else 0
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
            rec["missing_runs"] = 0  # feed churn, not a disappearance

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
