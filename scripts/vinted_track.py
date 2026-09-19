"""Track Vinted listings for the ten brands and confirm sales directly.

The previous watcher had to INFER sales: a listing vanished from the feed and a
pile of heuristics then argued about whether that meant sold, hidden, reserved,
deleted, or a seller on holiday. Most of its code was suppression.

That is no longer necessary. The item page states it:
    live  can_buy=true,  availability=InStock
    sold  can_buy=false, availability absent
Validated on 30 known-live (30/30 can_buy=true) and 30 known-sold (25/30
can_buy=false, 3 deleted, 1 unparseable, 1 apparently relisted). No live
listing showed can_buy=false, so the false-positive direction is clean.

Reserved listings are recorded separately rather than counted as sales: none
appeared in the validation sample, so "reserved implies not buyable but not
sold" is untested and must not be silently folded into the sold count.

Discovery uses catalog HTML, not the API - Vinted's /api/v2/ returns 404 to
every IP now, including a datacenter one, so it is gone rather than blocked.
Actions reads these pages unproxied, so this costs no proxy credit.
"""
from __future__ import annotations
import json, os, random, re, sys, time
import requests

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(REPO, "data", "vinted_track.json")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
BRANDS = ["Gucci", "Chanel", "Hermes", "Christian Louboutin", "Dior", "Prada",
          "Saint Laurent", "Valentino", "Golden Goose", "Bottega Veneta"]
# 10 pages = ~960 listings per brand, which is where Vinted stops paginating
# (page 20 returns nothing). At 3 pages, 93-98% of listings that left the feed
# turned out to be alive - they had simply been pushed past the window by newer
# listings, which is ranking noise, not a sale.
PAGES = int(os.environ.get("VT_PAGES", "10"))
# Measured, not guessed. 400 item fetches at a 1.0s gap came back 21% HTTP 429,
# flat across every bucket - a steady-state rate limit, not a burst that
# tightens. (A 20-request block at 0.4s passed 20/20, which is exactly why that
# test was not trusted: 20 requests fit inside the burst allowance.) At 2.0s,
# 150 consecutive fetches were clean. So the floor is set just above the last
# rate proven clean, and the pacer moves it if reality disagrees.
GAP_FLOOR = float(os.environ.get("VT_GAP", "2.5"))
GAP_CEIL = 20.0
MAX_CHECK = int(os.environ.get("VT_MAX_CHECK", "2800"))
# The real limit is the clock, not the count: the Actions job is killed at 180
# minutes and a killed job never reaches the state save, losing the whole
# cycle. So checking stops with time to spare and whatever was not reached
# simply leads the queue next cycle - nothing is lost by stopping early.
BUDGET_MIN = float(os.environ.get("VT_BUDGET_MIN", "150"))
# Recheck a listing at most this often; absent-from-feed listings jump the queue.
RECHECK_H = float(os.environ.get("VT_RECHECK_H", "18"))


class Pacer:
    """Self-correcting request spacing.

    The clean gap was measured over 150 requests; a full cycle is ~20x longer,
    so the limit could refill more slowly than that sample could show. Rather
    than bet the cycle on one number, back off hard on a 429 and drift back
    down only after sustained success. If 2.5s turns out to be too fast the
    watcher slows itself down instead of burning its budget on rejections.
    """

    def __init__(self, gap: float = GAP_FLOOR) -> None:
        self.gap = gap
        self.ok_streak = 0
        self.throttled = 0

    def wait(self) -> None:
        time.sleep(random.uniform(self.gap, self.gap * 1.4))

    def saw(self, status: int | None) -> None:
        if status == 429:
            self.throttled += 1
            self.ok_streak = 0
            self.gap = min(GAP_CEIL, self.gap * 1.8)
            print(f"    429 - slowing to {self.gap:.1f}s", flush=True)
            time.sleep(30)
        elif status == 200:
            self.ok_streak += 1
            # Only creep back down after a long clean run, and never below the
            # rate that was actually proven clean.
            if self.ok_streak >= 50 and self.gap > GAP_FLOOR:
                self.gap = max(GAP_FLOOR, self.gap * 0.9)
                self.ok_streak = 0


PACE = Pacer()


def get(s, url, **kw):
    PACE.wait()
    try:
        r = s.get(url, timeout=60, **kw)
    except requests.RequestException as e:
        print(f"    {type(e).__name__}", flush=True)
        return None
    PACE.saw(r.status_code)
    return r


def _parse(t, found):
    """Pull ids, prices and one image each out of a catalog page."""
    ids = re.findall(r"/items/(\d{6,12})-([a-z0-9-]{3,60})", t)
    pmap = dict(re.findall(
        r'\\?"id\\?":(\d{6,12}),.{0,400}?\\?"amount\\?":\\?"([\d.]+)', t))
    imgs = dict(re.findall(
        r'\\?"id\\?":(\d{6,12}),.{0,3000}?(https://images\d*\.vinted\.net/[^"\\\s]+/f800/[^"\\\s]+)', t))
    fresh = 0
    for iid, slug in ids:
        if iid not in found:
            found[iid] = {"slug": slug, "price": pmap.get(iid),
                          "img": imgs.get(iid)}
            fresh += 1
    return len(ids), fresh


def sweep(s, brand, bands=None):
    """One brand's current listings: id -> price, image, title.

    Vinted stops paginating at ~960 results, so a single query per brand has a
    hard ceiling no number of pages can lift. Price bands each get their own
    depth, which is how the corpus grows past it; a probe confirmed the price
    filter is honoured exactly, so the bands really do partition.

    Bands overlap slightly at their shared edges (Vinted treats both ends as
    inclusive, and prices cluster on round numbers like exactly 45 or 60), so
    ids are deduplicated across bands - measured at 6-14% duplication, which
    costs a little budget but loses nothing.
    """
    found = {}
    spans = [(b, bands[i + 1]) for i, b in enumerate(bands[:-1])] if bands \
        else [(None, None)]
    for lo, hi in spans:
        for page in range(1, PAGES + 1):
            par = {"search_text": f"{brand} shoes", "page": page}
            if lo is not None:
                par["currency"] = "EUR"
                if lo:
                    par["price_from"] = lo
                if hi and hi < 100000:
                    par["price_to"] = hi
            r = get(s, "https://www.vinted.it/catalog", params=par)
            if r is None or r.status_code != 200:
                break
            n, fresh = _parse(r.text, found)
            # Stop as soon as a page adds nothing new: a band shallower than
            # PAGES otherwise burns a full page request per empty page, and
            # with ten brands times six bands that waste is the whole budget.
            if n == 0 or fresh == 0:
                break
    return found


def check_state(s, iid, slug):
    """Direct read of the listing's state - no inference."""
    r = get(s, f"https://www.vinted.it/items/{iid}-{slug}")
    if r is None:
        return "error", {}
    if r.status_code in (404, 410):
        return "deleted", {}
    if r.status_code != 200:
        return f"http_{r.status_code}", {}
    t = r.text
    can = re.search(r'\\?"can_buy\\?":\s*(true|false)', t)
    res = re.search(r'\\?"is_reserved\\?":\s*(true|false)', t)
    av = re.search(r'"availability":"([^"]+)"', t)
    det = {"reserved": (res.group(1) == "true") if res else None,
           "availability": av.group(1) if av else None}
    if can is None:
        return "unknown", det
    if can.group(1) == "true":
        return "live", det
    return ("reserved" if det["reserved"] else "sold"), det


def main() -> int:
    try:
        st = json.load(open(STATE))
    except (OSError, ValueError):
        st = {"cycle": 0, "tracked": {}, "sold": {}, "events": []}
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
    s.get("https://www.vinted.it/", timeout=45)
    started = time.time()
    now = int(time.time())
    st["cycle"] += 1

    bands = {}
    if os.environ.get("VT_BANDS", "1") != "0":
        try:
            bands = json.load(open(os.path.join(REPO, "data", "vinted_bands.json")))
        except (OSError, ValueError):
            print("  no band file - falling back to unbanded search", flush=True)

    seen_now = {}
    for b in BRANDS:
        f = sweep(s, b, bands.get(b))
        for iid, v in f.items():
            v["brand"] = b
            seen_now[iid] = v
        print(f"  {b:<20} {len(f):>5} listings"
              f"{'' if b in bands else '  (unbanded)'}", flush=True)
    print(f"total distinct: {len(seen_now):,}", flush=True)

    absent = [i for i in st["tracked"] if i not in seen_now]
    print(f"\ntracked {len(st['tracked']):,}, absent from feed {len(absent):,}",
          flush=True)

    # State is now READ, not inferred. Disappearance is only a priority hint:
    # absent listings are likelier to have sold, but presence proves nothing
    # either - a listing can sell while still sitting in a cached feed page.
    # So every tracked listing is rechecked on rotation, absent ones first,
    # then whichever has gone longest without a check.
    def priority(i):
        r = st["tracked"][i]
        return (0 if i in absent_set else 1, r.get("last_check", 0))
    absent_set = set(absent)
    due = [i for i in st["tracked"]
           if i in absent_set
           or now - st["tracked"][i].get("last_check", 0) > RECHECK_H * 3600]
    due.sort(key=priority)
    batch = due[:MAX_CHECK]
    print(f"due for a state check: {len(due):,}, checking {len(batch)}", flush=True)

    # Split verdicts by WHY the listing was checked. Rotation is what makes a
    # full pass take days; if listings still present in the feed essentially
    # never come back sold, then rotation is buying nothing and the budget
    # belongs entirely to absent listings. That is a question about the data,
    # not a matter of opinion, so record it rather than assume either way.
    verdicts = {}
    split = {"absent": {}, "rotation": {}}
    deadline = started + BUDGET_MIN * 60
    stopped_early = 0
    for n_done, iid in enumerate(batch):
        if time.time() > deadline:
            stopped_early = len(batch) - n_done
            print(f"  budget reached - {stopped_early} checks deferred to "
                  f"next cycle", flush=True)
            break
        rec = st["tracked"][iid]
        v, det = check_state(s, iid, rec.get("slug", ""))
        verdicts[v] = verdicts.get(v, 0) + 1
        origin = "absent" if iid in absent_set else "rotation"
        split[origin][v] = split[origin].get(v, 0) + 1
        rec["last_check"] = now
        if v == "sold":
            st["sold"][iid] = {**rec, "sold_seen": now,
                               "availability": det.get("availability"),
                               "was_absent": iid in absent_set}
            st["tracked"].pop(iid, None)          # terminal
        elif v == "deleted":
            st["tracked"].pop(iid, None)          # terminal, not a sale
    if verdicts:
        print("  verdicts:", verdicts, flush=True)
        for o in ("absent", "rotation"):
            n = sum(split[o].values())
            if n:
                sold = split[o].get("sold", 0)
                print(f"    {o:<9} {n:>4} checked -> {sold} sold "
                      f"({100*sold/n:.1f}%)  {split[o]}", flush=True)

    for iid, v in seen_now.items():
        if iid not in st["tracked"] and iid not in st["sold"]:
            v["first_seen"] = now
            v["last_check"] = 0
            st["tracked"][iid] = v

    st["events"].append({"at": now, "cycle": st["cycle"],
                         "seen": len(seen_now), "absent": len(absent),
                         "checked": len(batch), "verdicts": verdicts,
                         "split": split, "gap": round(PACE.gap, 2),
                         "throttled": PACE.throttled,
                         "deferred": stopped_early,
                         "mins": round((time.time() - started) / 60, 1)})
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(st, open(STATE, "w"), separators=(",", ":"))
    print(f"\ncycle {st['cycle']}: tracking {len(st['tracked']):,}, "
          f"confirmed sold so far {len(st['sold']):,}", flush=True)
    print(f"pacing: final gap {PACE.gap:.1f}s, {PACE.throttled} throttled, "
          f"{(time.time() - started)/60:.0f} min used", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
