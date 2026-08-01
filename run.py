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

from vintedwatch import notify, report as report_mod, state as state_mod
from vintedwatch.client import VintedClient, parse_item

log = logging.getLogger("vintedwatch")

# One search per run, taken in turn. Every brand hits the same 960-item cap, so
# each needs its own sweep; doing all five in one run would fire 50 requests in
# a burst, which is the pattern that got our exits blocked. Rotating keeps every
# run at 10 requests — identical to the single-brand load that has been stable.
SEARCHES = [
    "prada shoes",
    "miu miu shoes",
    "maison margiela shoes",
    "christian louboutin shoes",
    "ferragamo shoes",
]
SEARCH_BASE = {"order": "newest_first", "per_page": 96}
# The whole result set is ~960 listings (the API reports total_entries), so at
# 96 per page the ENTIRE search is 10 requests. Sweeping all of it is the point:
# while we only watched the newest ~190, a listing could vanish from view merely
# by being outranked, and "gone" was unusable. With full coverage, gone is gone.
MAX_PAGES = 16              # safety stop; ~10 pages is the real depth
# Only a cost control, not an accuracy one: the wardrobe check is mandatory and
# authoritative, so this just decides when we spend a verification call.
GONE_AFTER_SWEEPS = 2       # complete sweeps a listing must miss before it counts as gone
AGED_OUT_AFTER = 4          # times a listing may be verified live-but-absent before we stop watching
MAX_CHECKS_PER_RUN = 6      # (HTML confirmation only; disabled — see CONFIRM_VIA_HTML)
# Vinted blocks .it HTML from our proxy exits, and the owner would rather treat a
# vanished listing as sold and eyeball the link than have the watcher fight for
# access. Absence from a complete sweep is the signal; no page fetch is made.
CONFIRM_VIA_HTML = False
SPREAD_MINUTES = 40         # spread those checks across the hour, never in a burst
MIN_RUN_GAP_S = 25 * 60     # refuse to poll again sooner than this, whatever triggers us
MAX_TRACK_DAYS = 30         # give up on a listing that never sells
UNKNOWN_GIVE_UP = 3         # consecutive failed confirmations before backing off
BLOCK_BACKOFF_H = 3         # first stand-down after a 403 wall; doubles while it persists
MAX_BACKOFF_H = 24          # ceiling for that doubling
STALE_ALERT_H = 8           # warn on Telegram if we have been unable to confirm this long
# Seller counters are the only remaining way to tell SOLD from HIDDEN/REMOVED.
# They need a baseline from BEFORE the listing vanished, so a slice of sellers is
# refreshed every run (~4 KB each) rather than looked up on demand.
SELLER_REFRESH_PER_RUN = 120   # ~4 KB each; ~3800 sellers get a baseline daily
MAX_GONE_CHECKS = 25           # cap on wardrobe scans (up to ~6 MB EACH)
CLASSIFY_VIA_COUNTERS = True
DAY = 86400


def fetch_feed(client: VintedClient, search_text: str) -> tuple[dict[int, dict], int | None, bool]:
    """Sweep one search. Returns {id: item}, reported total, complete."""
    items: dict[int, dict] = {}
    complete = True
    total_pages = None
    page = 1
    while page <= MAX_PAGES:
        raw = client.search(dict(SEARCH_BASE, search_text=search_text), page=page)
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
        if len(raw) < SEARCH_BASE["per_page"] or (total_pages and page >= total_pages):
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
    log.info("sweep [%s]: %d listings over %d pages (API says %s)%s",
             search_text, len(items), page, expected, "" if complete else " — INCOMPLETE")
    return items, expected, complete


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="never send Telegram messages")
    ap.add_argument("--test-telegram", action="store_true", help="send a ping and exit")
    ap.add_argument("--force", action="store_true", help="ignore the minimum gap between polls")
    ap.add_argument("--no-spread", action="store_true", help="do not pace checks across the hour")
    ap.add_argument("--report-now", action="store_true", help="print/send the daily digest and exit")
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

    if args.report_now:
        text = report_mod.build(st, SEARCHES)
        if args.dry_run:
            print(text)
        else:
            notify.send(text)
        return 0

    # The absence counters were built while we only saw the newest ~190 of 960
    # listings, where "absent" mostly meant "outranked". They cannot be trusted
    # under full-sweep semantics, so reset them once on upgrade.
    if st.get("schema", 1) < 2:
        for rec in tracked.values():
            rec["missing_runs"] = 0
        st["schema"] = 2
        log.warning("schema 1 -> 2: cleared %d absence counters built from partial sweeps",
                    len(tracked))
    if st.get("schema", 1) < 3:
        # Everything tracked so far came from the single "prada shoes" search.
        # Without an owner a listing belongs to no sweep, so it would never be
        # looked for again and never confirmed.
        for rec in tracked.values():
            rec.setdefault("search", SEARCHES[0])
        st.setdefault("seeded_searches", [])
        if SEARCHES[0] not in st["seeded_searches"]:
            st["seeded_searches"].append(SEARCHES[0])
        st["schema"] = 3
        log.warning("schema 2 -> 3: assigned %d existing listings to [%s]",
                    len(tracked), SEARCHES[0])

    since = now - st.get("last_run", 0)
    if not first_run and since < MIN_RUN_GAP_S and not args.force:
        # A burst of polls is exactly what gets an IP blocked. Trigger frequency
        # is someone else's decision; the safe interval is enforced here.
        log.info("last poll was %.0f min ago — skipping (min gap %d min)",
                 since / 60, MIN_RUN_GAP_S // 60)
        return 0

    # Round-robin: one brand per run. With a 30-minute trigger each brand is
    # swept every 2.5 h, while any single run stays at ~10 requests.
    idx = st.get("search_index", 0) % len(SEARCHES)
    search_text = SEARCHES[idx]
    seeded_searches = st.setdefault("seeded_searches", [])
    first_sweep_of_search = search_text not in seeded_searches
    log.info("this run sweeps [%s] (%d/%d)%s", search_text, idx + 1, len(SEARCHES),
             " — first sweep, seeding only" if first_sweep_of_search else "")

    client = VintedClient(token_cache=st.get("token"))
    live, floor, complete = fetch_feed(client, search_text)
    if not live:
        log.error("empty sweep for [%s] — aborting without touching state", search_text)
        return 1

    # --- absorb the feed ------------------------------------------------
    seeded = skipped = 0
    for item_id, item in live.items():
        key = str(item_id)
        if key in tracked:
            # Seen in ANY sweep means present — a listing can match two brands.
            tracked[key].update(item)
            tracked[key]["last_seen"] = now
            tracked[key]["missing_runs"] = 0
            continue
        # `seen_as_new` means we watched it appear, so first_seen is close to its
        # listing time. Everything in a search's FIRST sweep was already on sale,
        # so claiming a time-to-sale for those would be fiction.
        tracked[key] = dict(item, first_seen=now, last_seen=now, missing_runs=0,
                            last_check=0, seen_as_new=not first_sweep_of_search,
                            search=search_text)
        seeded += 1
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    day = st.setdefault("daily", {}).setdefault(today, {}).setdefault(search_text, {})
    if not first_sweep_of_search:
        day["new"] = day.get("new", 0) + seeded
    if first_sweep_of_search:
        log.info("seeded %d listings for [%s] (no alerts from a first sweep)",
                 seeded, search_text)
    elif seeded:
        log.info("%d new listings now tracked (silently — no alert)", seeded)

    if complete:
        # Only listings belonging to THIS search were looked for, so only they
        # can be counted absent. Anything owned by another brand is untouched.
        for key, rec in tracked.items():
            if rec.get("search") == search_text and int(key) not in live:
                rec["missing_runs"] = rec.get("missing_runs", 0) + 1
        mine = sum(1 for r in tracked.values() if r.get("search") == search_text)
        absent = sum(1 for r in tracked.values()
                     if r.get("search") == search_text and r.get("missing_runs", 0))
        log.info("[%s]: %d tracked, %d absent from this sweep", search_text, mine, absent)

    # --- keep seller baselines fresh -------------------------------------
    sellers = st.setdefault("sellers", {})
    if CLASSIFY_VIA_COUNTERS and complete:
        owned = {str(r["seller_id"]) for r in tracked.values() if r.get("seller_id")}
        # Never-seen sellers first, then the stalest. A baseline is only useful
        # if it predates the disappearance, so coverage matters more than age.
        todo = sorted(owned, key=lambda s: sellers.get(s, {}).get("at", 0))
        refreshed = 0
        for sid in todo[:SELLER_REFRESH_PER_RUN]:
            snap = client.seller_counters(int(sid))
            if snap:
                sellers[sid] = snap
                refreshed += 1
            time.sleep(random.uniform(0.4, 1.2))
        stale = sum(1 for s in owned if not sellers.get(s))
        log.info("seller baselines: refreshed %d, %d of %d sellers still without one",
                 refreshed, stale, len(owned))

    # --- decide what is gone ---------------------------------------------
    # No page fetches. A complete sweep already answers the only question that
    # matters: is the listing still in the search or not.
    sold_msgs, drop = [], []
    if not complete:
        log.warning("sweep incomplete — absence proves nothing this run, skipping")
    else:
        st["last_complete_sweep"] = now
        st.pop("stale_alerted", None)
        gone = [] if first_sweep_of_search else [
            rec for key, rec in tracked.items()
            if rec.get("search") == search_text and int(key) not in live
            and rec.get("missing_runs", 0) >= GONE_AFTER_SWEEPS]
        if gone:
            log.info("%d listings absent from %d consecutive complete sweeps",
                     len(gone), GONE_AFTER_SWEEPS)
        counters_cache: dict[str, dict] = {}
        checked = 0
        for rec in gone:
            key = str(rec["id"])
            verdict = "sold"

            # CHEAP TEST FIRST. A wardrobe scan walks every page of a seller's
            # listings — up to ~6 MB — while the seller's counters are 4 KB. On
            # 2026-08-01, 75 candidates in one run drove traffic to 15.5 MB
            # metered (22 GB/month) because every one of them scanned a wardrobe
            # before anything cheaper was tried.
            reason, delta = "unknown", None
            if CLASSIFY_VIA_COUNTERS and rec.get("seller_id"):
                sid = str(rec["seller_id"])
                before = sellers.get(sid)
                if sid not in counters_cache:
                    snap = client.seller_counters(int(sid))
                    if snap:
                        counters_cache[sid] = snap
                after = counters_cache.get(sid)
                if before and after and before.get("given") is not None:
                    delta = (after.get("given") or 0) - (before.get("given") or 0)
                    reason = "sold" if delta > 0 else "gone"
                if after:
                    sellers[sid] = after
                if reason == "gone":
                    # POSITIVE evidence of no sale: this seller has parted with
                    # nothing since the baseline, so the listing was hidden,
                    # reserved or deleted. Keep tracking it rather than dropping
                    # it — a hidden listing can come back — but say nothing.
                    log.info("%s not sold (seller given delta=0) — hidden/reserved/removed", key)
                    st.setdefault("suppressed", {})[key] = {
                        "at": now, "reason": "not_sold", "search": rec.get("search"),
                    }
                    rec["missing_runs"] = 0
                    continue

            if checked >= MAX_GONE_CHECKS:
                log.info("reached %d wardrobe scans this run — %s waits for the next",
                         MAX_GONE_CHECKS, key)
                continue
            checked += 1

            # The wardrobe is the ONLY authority here, so verification is
            # mandatory. `total_entries` is capped at 960 for every query (the
            # same number comes back for "nike" and "shoes"), so the sweep is a
            # window, not the whole search: an old listing ages out of it while
            # staying perfectly live. Absence alone therefore proves nothing.
            listed = client.still_listed(rec["id"], rec.get("seller_id"))
            if listed is True:
                # Live. Do NOT retire on the strength of one disappearance: the
                # feed is not ordered strictly by age (page 1 spans ids
                # 9421425269..9517979705), so bumping reshuffles listings and a
                # live, recent one can miss three sweeps by pure chance —
                # 9515145305 did, and retiring it lost a listing we should still
                # have been watching. Only persistent absence means aged out.
                rec["missing_runs"] = 0
                seen_live = rec.get("live_while_absent", 0) + 1
                rec["live_while_absent"] = seen_live
                if seen_live >= AGED_OUT_AFTER:
                    log.info("%s live but absent %d times — genuinely outside the window, retiring",
                             key, seen_live)
                    drop.append(key)
                else:
                    log.info("%s still live (%d/%d) — sweep missed it, keeping it",
                             key, seen_live, AGED_OUT_AFTER)
                continue
            if listed is None:
                # Could not verify: no seller_id, wardrobe too large, or the call
                # failed. Never alert on a guess — that is what leaked before.
                log.info("%s unverifiable (seller_id=%s) — no alert",
                         key, rec.get("seller_id"))
                if not rec.get("seller_id"):
                    drop.append(key)  # legacy record, cannot ever be verified
                continue
            time.sleep(random.uniform(1.5, 4.0))
            if CONFIRM_VIA_HTML:
                verdict = client.check_sold(rec["id"], rec.get("url"))
                log.info("check %s -> %s", key, verdict)
                if verdict == "live":
                    rec["missing_runs"] = 0
                    continue
                if verdict == "unknown":
                    continue  # try again next sweep

            log.info("%s -> ALERT (reason=%s, given delta=%s)", key, reason, delta)

            # Elapsed time is only honest for listings we watched arrive; the
            # rest were already on sale when tracking began.
            listed = rec.get("first_seen")
            hours = (now - listed) / 3600 if listed else None
            exact = bool(rec.get("seen_as_new"))
            # A counter-confirmed sale is no longer a guess, so drop "probabile".
            sold_msgs.append((rec, hours, exact,
                              not CONFIRM_VIA_HTML and reason != "sold"))
            st["sold"][key] = {
                "reported_at": now,
                "search": rec.get("search"),
                "title": rec.get("title"),
                "price": rec.get("price"),
                "currency": rec.get("currency"),
                "url": rec.get("url"),
                "hours_listed": round(hours, 1) if hours else None,
                "hours_exact": exact,
                "confirmed": bool(CONFIRM_VIA_HTML) or reason == "sold",
                "given_delta": delta,
            }
            day["sales"] = day.get("sales", 0) + 1
            try:
                day["price_sum"] = round(day.get("price_sum", 0.0) + float(rec.get("price") or 0), 2)
            except (TypeError, ValueError):
                pass
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

    if complete and first_sweep_of_search:
        seeded_searches.append(search_text)
    if report_mod.due(st):
        text = report_mod.build(st, SEARCHES)
        if args.dry_run:
            log.info("[dry-run] daily digest:\n%s", text)
        else:
            notify.send(text)
            st["last_report_date"] = report_mod.local_now().strftime("%Y-%m-%d")
        log.info("daily digest sent")

    # Keep individual sale records for a week (the digest only looks back 24 h);
    # the per-day tallies in st["daily"] carry the longer history compactly.
    cutoff = now - 7 * DAY
    for k in [k for k, v in st.get("suppressed", {}).items() if v.get("at", 0) < cutoff]:
        st["suppressed"].pop(k)
    stale = [k for k, v in st["sold"].items() if v.get("reported_at", 0) < cutoff]
    for k in stale:
        st["sold"].pop(k)
    for day in [d for d in st.get("daily", {}) if d < time.strftime("%Y-%m-%d", time.gmtime(now - 90 * DAY))]:
        st["daily"].pop(day)
    if stale:
        log.info("pruned %d sale records older than 7 days (%d kept)", len(stale), len(st["sold"]))

    st["search_index"] = (idx + 1) % len(SEARCHES)
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
