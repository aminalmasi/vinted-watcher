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
import random
import sys
import time

from vintedwatch import notify, state as state_mod
from vintedwatch.client import VintedClient, parse_item

log = logging.getLogger("vintedwatch")

SEARCH = {
    "search_text": "prada shoes",
    "order": "newest_first",
    "per_page": 96,
}
# The whole result set is ~960 listings (the API reports total_entries), so at
# 96 per page the ENTIRE search is 10 requests. Sweeping all of it is the point:
# while we only watched the newest ~190, a listing could vanish from view merely
# by being outranked, and "gone" was unusable. With full coverage, gone is gone.
MAX_PAGES = 16              # safety stop; ~10 pages is the real depth
SEED_DAYS = 5               # first run: only remember the last 5 days
GONE_AFTER_SWEEPS = 2       # complete sweeps a listing must miss before it counts as gone
MAX_CHECKS_PER_RUN = 6      # (HTML confirmation only; disabled — see CONFIRM_VIA_HTML)
# Vinted blocks .it HTML from our proxy exits, and the owner would rather treat a
# vanished listing as sold and eyeball the link than have the watcher fight for
# access. Absence from a complete sweep is the signal; no page fetch is made.
CONFIRM_VIA_HTML = False
SPREAD_MINUTES = 40         # spread those checks across the hour, never in a burst
MIN_RUN_GAP_S = 50 * 60     # refuse to poll again sooner than this, whatever triggers us
MAX_TRACK_DAYS = 30         # give up on a listing that never sells
UNKNOWN_GIVE_UP = 3         # consecutive failed confirmations before backing off
BLOCK_BACKOFF_H = 3         # first stand-down after a 403 wall; doubles while it persists
MAX_BACKOFF_H = 24          # ceiling for that doubling
STALE_ALERT_H = 8           # warn on Telegram if we have been unable to confirm this long
DAY = 86400


def fetch_feed(client: VintedClient) -> tuple[dict[int, dict], int | None, bool]:
    """Read the newest pages of the search. Returns {id: item}, age floor, complete."""
    items: dict[int, dict] = {}
    complete = True
    total_pages = None
    page = 1
    while page <= MAX_PAGES:
        raw = client.search(SEARCH, page=page)
        if raw is None:
            # One failed page means we no longer hold the whole set, so an
            # absence this run proves nothing. Say so and let the run skip it.
            log.warning("page %d failed — sweep is incomplete", page)
            complete = False
            break
        if total_pages is None:
            total_pages = (client.last_pagination or {}).get("total_pages")
        if not raw:
            break
        for r in raw:
            parsed = parse_item(r)
            if parsed["id"]:
                items[parsed["id"]] = parsed
        if len(raw) < SEARCH["per_page"] or (total_pages and page >= total_pages):
            break
        page += 1
        # Jittered, not a metronome — fixed intervals are themselves a signal.
        time.sleep(random.uniform(2.0, 6.0))
    else:
        # Hit MAX_PAGES without reaching the end: coverage is partial.
        complete = False
        log.warning("stopped at MAX_PAGES=%d before the end of the results", MAX_PAGES)

    expected = (client.last_pagination or {}).get("total_entries")
    if complete and expected and len(items) < expected * 0.9:
        # Sanity check: the API says there are more listings than we collected.
        log.warning("swept %d listings but the API reports %d — treating as incomplete",
                    len(items), expected)
        complete = False
    log.info("sweep: %d listings over %d pages (API says %s)%s",
             len(items), page, expected, "" if complete else " — INCOMPLETE")
    return items, expected, complete


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
    ap.add_argument("--force", action="store_true", help="ignore the minimum gap between polls")
    ap.add_argument("--no-spread", action="store_true", help="do not pace checks across the hour")
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

    # The absence counters were built while we only saw the newest ~190 of 960
    # listings, where "absent" mostly meant "outranked". They cannot be trusted
    # under full-sweep semantics, so reset them once on upgrade.
    if st.get("schema", 1) < 2:
        for rec in tracked.values():
            rec["missing_runs"] = 0
        st["schema"] = 2
        log.warning("schema 1 -> 2: cleared %d absence counters built from partial sweeps",
                    len(tracked))

    since = now - st.get("last_run", 0)
    if not first_run and since < MIN_RUN_GAP_S and not args.force:
        # A burst of polls is exactly what gets an IP blocked. Trigger frequency
        # is someone else's decision; the safe interval is enforced here.
        log.info("last poll was %.0f min ago — skipping (min gap %d min)",
                 since / 60, MIN_RUN_GAP_S // 60)
        return 0

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
        # `seen_as_new` means we watched it appear, so first_seen really is
        # close to its listing time. Seeded listings were already on sale.
        tracked[key] = dict(item, first_seen=now, last_seen=now,
                            missing_runs=0, last_check=0, seen_as_new=not first_run)
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

    # --- decide what is gone ---------------------------------------------
    # No page fetches. A complete sweep already answers the only question that
    # matters: is the listing still in the search or not.
    sold_msgs, drop = [], []
    if not complete:
        log.warning("sweep incomplete — absence proves nothing this run, skipping")
    else:
        st["last_complete_sweep"] = now
        st.pop("stale_alerted", None)
        gone = [rec for key, rec in tracked.items()
                if int(key) not in live and rec.get("missing_runs", 0) >= GONE_AFTER_SWEEPS]
        if gone:
            log.info("%d listings absent from %d consecutive complete sweeps",
                     len(gone), GONE_AFTER_SWEEPS)
        for rec in gone:
            key = str(rec["id"])
            verdict = "sold"
            if CONFIRM_VIA_HTML:
                verdict = client.check_sold(rec["id"], rec.get("url"))
                log.info("check %s -> %s", key, verdict)
                if verdict == "live":
                    rec["missing_runs"] = 0
                    continue
                if verdict == "unknown":
                    continue  # try again next sweep

            # Elapsed time is only honest for listings we watched arrive; the
            # rest were already on sale when tracking began.
            listed = rec.get("first_seen")
            hours = (now - listed) / 3600 if listed else None
            exact = bool(rec.get("seen_as_new"))
            sold_msgs.append((rec, hours, exact, verdict == "sold" and not CONFIRM_VIA_HTML))
            st["sold"][key] = {
                "reported_at": now,
                "title": rec.get("title"),
                "price": rec.get("price"),
                "currency": rec.get("currency"),
                "url": rec.get("url"),
                "hours_listed": round(hours, 1) if hours else None,
                "hours_exact": exact,
                "confirmed": bool(CONFIRM_VIA_HTML),
            }
            drop.append(key)

    for key in drop:
        tracked.pop(key, None)

    # --- retire listings that will never resolve -------------------------
    for key, rec in list(tracked.items()):
        if now - (rec.get("first_seen") or now) > MAX_TRACK_DAYS * DAY:
            tracked.pop(key, None)

    # --- notify -----------------------------------------------------------
    for rec, hours, exact, probable in sold_msgs:
        text = notify.format_sold(rec, hours, exact=exact, probable=probable)
        if args.dry_run:
            log.info("[dry-run] would send:\n%s", text)
        else:
            notify.send(text)
    log.info("SOLD this run: %d", len(sold_msgs))

    # Silence must never be ambiguous: if no complete sweep has succeeded for a
    # long time, the watcher is blind and should say so once.
    last_ok = st.get("last_complete_sweep", now)
    if now - last_ok > STALE_ALERT_H * 3600 and not st.get("stale_alerted"):
        hrs = (now - last_ok) / 3600
        notify.send(
            f"⚠️ <b>Watcher cieco</b>\nNessuna scansione completa da ~{hrs:.0f} h "
            f"(proxy o Vinted irraggiungibili).\n"
            f"{len(tracked)} annunci ancora in memoria: nulla è perso, "
            f"ma non posso rilevare vendite finché non torna."
        )
        st["stale_alerted"] = True
        log.warning("sent blind-state alert (%.0f h without a complete sweep)", hrs)

    st["token"] = client.token_cache
    st["last_run"] = now
    if args.dry_run:
        log.info("[dry-run] state NOT saved (%d tracked, %d sold would have been written)",
                 len(tracked), len(st["sold"]))
    else:
        state_mod.save(st)
    log.info("traffic this run: %.0f KB uncompressed (metered ~%.0f KB gzipped)",
             client.bytes_uncompressed / 1024, client.bytes_uncompressed / 1024 / 7)
    return 0


if __name__ == "__main__":
    sys.exit(main())
