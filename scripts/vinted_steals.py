"""Steals: listings of the ten brands that sell within 12 hours of upload.

The sold-tracker watches ~23k shoe listings for 60 days and cannot see a fast
sale well: a listing has to be discovered, then found missing, then checked,
and at T~1.3h a 4-hour sale is a coin toss. This watches only NEW listings, in
every category, for one day each, which is a much smaller job:

  sweep   each brand, searched by Vinted's brand ids (every variant, one
          query), newest first, paged only as deep as the oldest listing still
          being watched - about one day of uploads
  absent  a watched listing the sweep passed over without seeing
  check   only the absent ones get a page request, which says sold, reserved,
          deleted or still live
  retire  after WATCH_H hours every listing is closed out and written to the
          day's file, sold or not, so the unsold ones give the base rate

A listing counts as NEW only if its id is above the highest id the previous
sweep of that brand saw. Vinted ids are issued in upload order, so this also
rejects old listings that a promotion or bump pushes to the top of the feed.
The first cycle therefore only sets that frontier and watches nothing.

Interest is recorded on every sweep at no extra cost: the catalog card carries
the like count, so each listing keeps its likes over time. Offers and view
counts are not public.

State (small, rewritten each cycle):  data/steals/state.json
Closed listings (append-only per day): data/steals/days/YYYY-MM-DD.jsonl
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import time

import requests

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(REPO, "data", "steals")
STATE = os.path.join(DIR, "state.json")
BRANDS = os.path.join(DIR, "brands.json")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

WATCH_H = float(os.environ.get("VS_WATCH_H", "24"))
STEAL_H = float(os.environ.get("VS_STEAL_H", "12"))
PAGES = 10                       # Vinted serves at most 10 pages of 96
# The same gap discovery uses in the sold-tracker, where 2.5s searching was
# what poisoned whole cycles; both trackers draw on one shared allowance.
GAP = float(os.environ.get("VS_GAP", "5.0"))
MAX_REQ = int(os.environ.get("VS_MAX_REQ", "400"))
BLOCK_GIVEUP = 6
# Absent-but-live listings are rechecked at most this often; a search index
# that lags behind the item page would otherwise cost a request every cycle.
# Six hours, not three: some listings are live on their page yet shown by no
# search at all (3 of 8 probed misses), and each recheck of one is wasted.
RECHECK_H = 6.0
# A listing must be missing from MISSES_TO_CHECK sweeps in a row before its page
# is read. Reading the same feed twice in a row, 1.3% of listings appeared in
# one read and not the other - a sale or deletion between two page requests
# shifts every later listing up a place - which over ~8k watched listings is
# ~100 false absences a cycle. A real sale stays missing; a shifted listing
# reappears. Sale-time bounds stay honest: they run from the last sighting.
MISSES_TO_CHECK = 2

# Brands with more uploads a day than one search can show (960) are swept in
# price bands, each sized to ~450 uploads a day (data/steals/bands.json).
# Price, not category: Vinted honours the price filter exactly (probed: every
# listing in band), while category searches leak and, combined, silently drop
# listings - seven top-level categories in one search missed listings that
# each category alone showed on page 1, and two categories together returned
# 0 of one of them. A band partitions a brand cleanly, and a listing's band
# follows from its own price, so coverage needs no guessing.
BANDS = os.path.join(DIR, "bands.json")
# Brands narrowed to some of their Vinted brand titles. The plain "Valentino"
# id turned out to be 87% of Valentino uploads and almost all cheap bags sold
# under that label (median asking price EUR 50 for a handbag), so only
# "Valentino Garavani" is tracked from 2026-10-27. Watched listings whose
# card names another title are dropped, not closed: they never belonged.
TITLES = os.path.join(DIR, "titles.json")

# Most cycles sweep only the newest SHALLOW_H hours, where nearly all steals
# happen (median time to sale 1.2h; 10% of sales come after 12h); every
# DEEP_EVERY-th cycle sweeps the whole watch window, and only those retire
# listings at WATCH_H, so a listing is never closed "unsold" without a sweep
# having seen it alive.
SHALLOW_H = float(os.environ.get("VS_SHALLOW_H", "12"))
DEEP_EVERY = int(os.environ.get("VS_DEEP_EVERY", "4"))
HARD_END_H = WATCH_H + 6           # closed regardless, so state stays bounded
HISTORY_KEEP = 500                 # cycle summaries kept in the state file

# After a gap in the runs, the old frontier would admit every listing uploaded
# during the gap as "new", with an upload window as wide as the gap. That
# happened on 2026-11-21 after 15 days of no runs: ~20,000 old listings came
# in with 15-day windows, their sweeps had to reach back past the 960 cap,
# every band was cut short and no checks ran. So a unit not swept for
# REBASE_H hours re-baselines - its sweep sets the frontier and admits nothing
# - and a watched listing whose upload cannot be pinned within MAX_WINDOW_H
# is dropped: it gives no time to sale, so it is not data for this tracker.
REBASE_H = float(os.environ.get("VS_REBASE_H", "3"))
MAX_WINDOW_H = float(os.environ.get("VS_MAX_WINDOW_H", "6"))

# Vinted ids are one counter for the whole site, handed out at upload. So the
# highest id any sweep has seen by time t says every listing below it was
# uploaded before t - whatever its brand or band. Each cycle records
# [end time, highest id seen]; a listing claimed as uploaded after u must have
# an id above the clock at u. This checks the per-unit frontier bookkeeping
# from outside: on 2026-11-21 a unit's frontier stayed 15 days stale while its
# sweep time was refreshed, and 6,000 old listings were admitted at 12:34 with
# 40-minute windows - 12 of them closed as steals the next hours.
CLOCK_KEEP = 200


def clock_at(clock: list, t: float) -> int:
    return max((i for at, i in clock if at <= t), default=0)

# A cycle that runs out of time is cancelled by Actions and saves NOTHING -
# three in a row were lost that way. So the cycle stops itself first.
BUDGET_S = float(os.environ.get("VS_BUDGET_MIN", "21")) * 60
DEEP_BUDGET_S = float(os.environ.get("VS_DEEP_BUDGET_MIN", "34")) * 60
T_START = time.time()


class Pacer:
    def __init__(self) -> None:
        self.gap, self.count, self.streak, self.refused, self.ok = GAP, 0, 0, 0, 0

    @property
    def stop(self) -> bool:
        return (self.streak >= BLOCK_GIVEUP or self.count >= MAX_REQ
                or time.time() - T_START > BUDGET_S)

    def get(self, s, url, **kw):
        self.count += 1
        time.sleep(random.uniform(self.gap, self.gap * 1.3))
        try:
            r = s.get(url, timeout=60, **kw)
        except requests.RequestException as e:
            print(f"    {type(e).__name__}", flush=True)
            return None
        if r.status_code in (403, 429):
            self.refused += 1
            self.streak += 1
            self.gap = min(20.0, self.gap * 1.6)
            print(f"    HTTP {r.status_code} ({self.streak} in a row)", flush=True)
            time.sleep(30)
        elif r.status_code == 200:
            self.streak = 0
            self.ok += 1
            # Drift back toward the base gap after a long clean run, so one
            # refusal does not slow the rest of a long job for good.
            if self.ok % 30 == 0 and self.gap > GAP:
                self.gap = max(GAP, self.gap * 0.8)
        return r


PACE = Pacer()


# ------------------------------------------------------------------ parsing

PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
CARD_RE = re.compile(r'\{"id":(\d{6,12}),"productItem":')


def flight(html: str) -> str:
    """The page's data, decoded from the script payloads it streams it in."""
    out = []
    for lit in PUSH_RE.findall(html):
        try:
            out.append(json.loads(lit))
        except ValueError:
            pass
    return "".join(out)


def cards(html: str) -> list[dict]:
    """Every listing card on a catalog page, as structured records."""
    data, dec, out = flight(html), json.JSONDecoder(), []
    for m in CARD_RE.finditer(data):
        try:
            obj, _ = dec.raw_decode(data, m.start())
        except ValueError:
            continue
        p = obj.get("productItem") or {}
        box = p.get("itemBox") or {}
        photos = p.get("photos") or [{}]
        try:
            price = float((p.get("price") or {}).get("amount"))
        except (TypeError, ValueError):
            price = None
        out.append({
            "id": int(p.get("id") or obj["id"]),
            "url": p.get("url"),
            "title": p.get("title"),
            "price": price,
            "likes": p.get("favouriteCount"),
            "brand": box.get("firstLine"),
            "line2": box.get("secondLine"),     # size and/or condition
            # Not the seller's id: this repository is public, and review
            # count, rating and the business flag are what trust needs.
            "business": (p.get("user") or {}).get("isBusiness"),
            "photo": photos[0].get("url") if photos else None,
            "promoted": bool(p.get("isPromoted")) or
                        (obj.get("catalogTracking") or {}).get("contentSource", "").endswith("promoted_items"),
        })
    return out


UNITS = (("second", 1 / 3600), ("min", 1 / 60), ("ora", 1), ("ore", 1),
         ("giorn", 24), ("settiman", 168), ("mes", 730), ("ann", 8760))


def age_hours(text: str) -> float | None:
    """'19 min fa', "un'ora fa", '3 ore fa', 'un minuto fa' -> hours."""
    v = text.lower()
    n = re.search(r"(\d+)", v)
    k = int(n.group(1)) if n else 1
    for stem, h in UNITS:
        if stem in v:
            return round(k * h, 3)
    return None


def attributes(html: str) -> dict:
    """The listing's attribute block: Condizioni, Taglia, Colore, Caricato."""
    out = {}
    for m in re.finditer(r'data-testid="item-attributes-([a-z_]+)"', html):
        start = html.find(">", m.end()) + 1
        seg = html[start:start + 900]
        parts = [p.strip() for p in re.sub(r"<[^>]+>", "|", seg).split("|") if p.strip()]
        if len(parts) >= 2:              # [label, value, ...]
            out[m.group(1)] = parts[1]
    return out


def check(s, rec: dict) -> tuple[str, dict]:
    """Read a listing's page: its state, plus what only the page carries."""
    r = PACE.get(s, f"https://www.vinted.it{rec['url']}")
    if r is None:
        return "error", {}
    if r.status_code in (404, 410):
        return "deleted", {}
    if r.status_code != 200:
        return f"http_{r.status_code}", {}
    t = r.text.replace('\\"', '"')
    can = re.search(r'"can_buy":(true|false)', t)
    res = re.search(r'"is_reserved":(true|false)', t)

    def num(key, cast=int):
        m = re.search(r'"' + key + r'":"?([\d.]+)', t)
        return cast(m.group(1)) if m else None

    attrs = attributes(r.text)
    hid = re.search(r'"is_hidden":(true|false)', t)
    # Not "likes": that name holds the listing's like history, and the first
    # version overwrote it with this single count.
    det = {"catalog_id": num("catalog_id"), "brand_id": num("brand_id"),
           "likes_at_check": num("favourite_count"), "seller_reviews": num("feedback_count"),
           "hidden": (hid.group(1) == "true") if hid else None,
           "seller_rating": num("feedback_reputation", float),
           "condition": attrs.get("status"), "size": attrs.get("size"),
           "color": attrs.get("color"),
           "upload_ago_h": age_hours(attrs["upload_date"]) if "upload_date" in attrs else None}
    if can is None:
        # A removed listing comes back as HTTP 200 and the app shell.
        return ("gone" if '"item_id"' not in t else "unknown"), det
    if can.group(1) == "true":
        return "live", det
    return ("reserved" if res and res.group(1) == "true" else "sold"), det


# -------------------------------------------------------------------- sweep

def band_params(band) -> list:
    lo, hi = band
    out = [("currency", "EUR")] if (lo or hi) else []
    if lo:
        out.append(("price_from", lo))
    if hi:
        out.append(("price_to", hi))
    return out


def sweep(s, ids: list[int], band, stop_below: int | None) -> tuple[list[dict], int | None, bool]:
    """Newest-first pages for one brand (or one price band of it) until the
    feed is older than the oldest listing still watched.

    Returns the cards, the lowest id the sweep is known to have passed, and
    whether it got there. That floor is the page's MEDIAN id, and must stay
    the median. Newest-first feeds mix in old listings sellers have paid to
    push back to the top: probed, 3-27% of a page's ordinary listings are
    hours to months older than their neighbours (one Dior page held a
    143-day-old listing). Any rule that reads the low end of a page - the
    bottom, or 90% of the way down, as one version did - sees those and
    concludes the feed already reaches days back, so it stops after a page or
    two and every listing below goes "missing": 93% of the absences that
    version checked were still live. Fresh listings run in id order and fill
    well over half of every page, so the median is always one of them.
    """
    got: list[dict] = []
    params = band_params(band) + [("brand_ids[]", b) for b in ids] + [("order", "newest_first")]
    floor = None
    for page in range(1, PAGES + 1):
        if PACE.stop:
            return got, floor, False
        r = PACE.get(s, "https://www.vinted.it/catalog", params=params + [("page", page)])
        if r is None or r.status_code != 200:
            return got, floor, False
        cs = cards(r.text)
        got.extend(cs)
        organic = sorted((c["id"] for c in cs if not c["promoted"]), reverse=True) \
            or sorted((c["id"] for c in cs), reverse=True)
        if len(organic) < 48:              # the feed ran out: everything was shown
            return got, 0, True
        floor = organic[len(organic) // 2]
        if stop_below is None or floor < stop_below:
            return got, floor, True
    return got, floor, False


def band_of(price, edges: list) -> tuple:
    """The (lo, hi) band a price falls in; edges like [0, 35, 65, None]."""
    p = float(price or 0)
    for lo, hi in zip(edges, edges[1:]):
        if hi is None or p < hi:
            return (lo, hi)
    return (edges[-2], edges[-1])


def unit_name(brand: str, band, single: bool) -> str:
    return brand if single else f"{brand}|{band[0]}-{band[1] if band[1] is not None else ''}"


# -------------------------------------------------------------------- state

def load() -> dict:
    try:
        return json.load(open(STATE))
    except (OSError, ValueError):
        return {"frontier": {}, "watch": {}, "cycles": []}


def narrow_upload(rec: dict, ago_h: float, at: float) -> None:
    """Tighten the upload window with the page's own 'Caricato N ... fa'.

    Vinted truncates: '3 ore fa' means 3 to 4 hours ago, '19 min fa' 19 to 20
    minutes. Intersected with what the sweeps already bound, so a coarse
    reading can only help.
    """
    unit = 1 / 60 if ago_h < 1 else (1 if ago_h < 24 else 24)
    lo, hi = at - (ago_h + unit) * 3600, at - ago_h * 3600
    rec["up_lo"] = max(rec["up_lo"], lo)
    rec["up_hi"] = min(rec["up_hi"], hi)
    if rec["up_lo"] > rec["up_hi"]:        # readings disagree; keep the sweeps'
        rec["up_lo"], rec["up_hi"] = rec["up_after"], rec["first_seen"]


def close(rec: dict, outcome: str, now: float) -> dict:
    """Final record. Upload and sale times are windows, not points: uploaded
    within [up_lo, up_hi], sold after `last_live` and by `now`. The estimate
    and the worst case are both kept, so a 12-hour cut can be made either way.
    """
    rec = dict(rec, outcome=outcome, closed=now)
    if outcome == "sold":
        up = (rec["up_lo"] + rec["up_hi"]) / 2
        rec["sold_h_est"] = round(((rec["last_live"] + now) / 2 - up) / 3600, 2)
        rec["sold_h_min"] = round((rec["last_live"] - rec["up_hi"]) / 3600, 2)
        rec["sold_h_max"] = round((now - rec["up_lo"]) / 3600, 2)
        # A steal only when the sale time is known to within 4 hours. After a
        # gap in the runs the sale window spans the gap, and its midpoint can
        # fall under 12h for a listing that took longer; those are kept but
        # marked uncertain instead.
        spread = rec["sold_h_max"] - rec["sold_h_min"]
        rec["steal"] = rec["sold_h_est"] <= STEAL_H and spread <= 4
        rec["steal_uncertain"] = rec["sold_h_est"] <= STEAL_H and spread > 4
    return rec


def repair_1121(st: dict, watch: dict) -> int:
    """One-off: undo the 12:34 UTC run of 2026-11-21, which trusted a 15-day
    stale frontier and admitted ~6,000 old listings with 40-minute windows.

    The id clock cannot catch them - it has no point before that day's
    restart - so the line is drawn by hand: 10905728307 is the highest id the
    restart's first sweep (Gucci, 12:03:58 UTC) saw, so anything at or below
    it was uploaded before about 12:04, not after the run's ~11:56 claim; ids
    from those runs reach back to 10701298365, which is ~6 November. Above
    the line, 554 watched and 34 closed listings are kept as genuine.

    Dropped from the watch; in the day file the closed ones lose their steal
    flag and are marked window_bad. Every sold record of the day also gets
    the 4-hour spread rule, which the runs before 19:33 closed without.
    """
    if st.get("repair_1121"):
        return 0
    lo, hi, line = 1795264200, 1795266000, 10905728307
    bad = lambda r: lo <= r["first_seen"] < hi and int(r["id"]) <= line
    gone = [k for k, r in watch.items() if bad(r)]
    for k in gone:
        watch.pop(k)
    path = os.path.join(DIR, "days", "2026-11-21.jsonl")
    fixed = 0
    if os.path.exists(path):
        out = []
        for line_ in open(path):
            r = json.loads(line_)
            if r.get("outcome") == "sold":
                if bad(r):
                    r.update(steal=False, steal_uncertain=False, window_bad=True)
                    fixed += 1
                else:
                    spread = r["sold_h_max"] - r["sold_h_min"]
                    r["steal"] = r["sold_h_est"] <= STEAL_H and spread <= 4
                    r["steal_uncertain"] = r["sold_h_est"] <= STEAL_H and spread > 4
            elif bad(r):
                r["window_bad"] = True
            out.append(json.dumps(r, ensure_ascii=False, separators=(",", ":")))
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            fh.write("\n".join(out) + "\n")
        os.replace(tmp, path)
    st["repair_1121"] = {"watch_dropped": len(gone), "closed_marked": fixed}
    return len(gone)


def write_closed(recs: list[dict]) -> None:
    os.makedirs(os.path.join(DIR, "days"), exist_ok=True)
    by_day: dict[str, list] = {}
    for r in recs:
        day = time.strftime("%Y-%m-%d", time.gmtime(r["first_seen"]))
        by_day.setdefault(day, []).append(r)
    for day, rs in by_day.items():
        with open(os.path.join(DIR, "days", f"{day}.jsonl"), "a") as fh:
            for r in rs:
                fh.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> int:
    global T_START, BUDGET_S
    T_START = time.time()
    groups = json.load(open(BRANDS))          # {"Gucci": [567, ...], ...}
    st = load()
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
    s.get("https://www.vinted.it/", timeout=45)
    now = time.time()
    ev = {"at": int(now), "new": 0, "absent": 0, "checked": 0, "closed": {},
          "swept": {}, "incomplete": []}
    watch, frontier, closed = st["watch"], st["frontier"], []
    try:
        titles = json.load(open(TITLES))
    except (OSError, ValueError):
        titles = {}
    off = [k for k, r in watch.items() if r["group"] in titles and r.get("brand") not in titles[r["group"]]]
    for k in off:
        watch.pop(k)
    ev["dropped_title"] = len(off)
    wide = [k for k, r in watch.items() if r["first_seen"] - r.get("up_lo", r["first_seen"]) > MAX_WINDOW_H * 3600]
    for k in wide:
        watch.pop(k)
    ev["dropped_wide_window"] = len(wide)
    clock = st.get("id_clock") or []
    old = [k for k, r in watch.items() if int(r["id"]) <= clock_at(clock, r.get("up_after", r["first_seen"]))]
    for k in old:
        watch.pop(k)
    ev["dropped_old_id"] = len(old)
    ev["repaired_1121"] = repair_1121(st, watch)
    ev["rebased"] = []
    ev["old_by_clock"] = 0

    # Its own counter. This was len(st["cycles"]), but that history is capped
    # at HISTORY_KEEP + 1 entries, so once full the length froze at 501 -
    # 501 % 4 == 1 - and no deep cycle ran from 2026-11-01 11:03 on. Listings
    # past 12h were then never swept again: sales 12-24h after upload went
    # unseen and 24,314 listings were closed "unresolved". A state without the
    # counter starts at 0, so the first run after this fix is a deep one.
    cycle_no = st.get("cycle_no", 0)
    deep = cycle_no % DEEP_EVERY == 0
    ev["deep"] = deep
    shallow_cut = now - SHALLOW_H * 3600

    # Sweep units: a whole brand, or one price band of it.
    try:
        bands = json.load(open(BANDS))
    except (OSError, ValueError):
        bands = {}
    edges = {b: bands.get(b) or [0, None] for b in groups}
    units = []
    for brand, ids in groups.items():
        e = edges[brand]
        for band in zip(e, e[1:]):
            units.append((unit_name(brand, band, len(e) == 2), brand, ids, band))

    def unit_of(r: dict) -> str:
        e = edges[r["group"]]
        return unit_name(r["group"], band_of(r.get("price_now") or r.get("price"), e), len(e) == 2)

    seen_now: dict[str, dict] = {}
    passed: dict[str, int] = {}
    cycle_top = 0
    BUDGET_S = DEEP_BUDGET_S if deep else float(os.environ.get("VS_BUDGET_MIN", "21")) * 60
    for unit, brand, ids, band in units:
        mine = [r for r in watch.values() if r["group"] == brand and unit_of(r) == unit
                and (deep or r["first_seen"] >= shallow_cut)]
        swept_at = st.get("swept_at") or {}
        mine_keys = [k for k in frontier if k == brand or k.startswith(brand + "|")]
        # A unit's own frontier is only trusted if that unit was swept lately.
        # A new unit, or a renamed one - Valentino went from four price bands
        # back to one unit called "Valentino", whose frontier dated from a week
        # earlier - starts from its brand's highest frontier instead: a listing
        # missed as new for one cycle beats a week of old ones taken for new.
        if frontier.get(unit) and swept_at.get(unit, 0) > now - 2 * 3600:
            prev, prev_at = frontier[unit], swept_at[unit]
        else:
            prev = max([frontier[k] for k in mine_keys] or [0]) or None
            prev_at = max([swept_at.get(k, 0) for k in mine_keys] or [0]) or None
        stop_below = min(int(r["id"]) for r in mine) if mine else prev
        t0 = time.time()
        rebase = prev_at is None or t0 - prev_at > REBASE_H * 3600
        if rebase:
            ev["rebased"].append(unit)
            stop_below = None              # just read page one and set the frontier
        got, floor, complete = sweep(s, ids, band, stop_below)
        ev["swept"][unit] = len(got)
        if not complete:
            ev["incomplete"].append(unit)
        if floor is not None:
            passed[unit] = floor
        top = max((c["id"] for c in got if not c["promoted"]), default=None)
        for c in got:
            k = str(c["id"])
            if k in seen_now:
                continue                       # leaked into a second group
            if k in watch:
                rec = watch[k]
                rec["misses"] = 0
                if not isinstance(rec.get("likes"), list):     # repair the first version's overwrite
                    rec["likes"] = [[int(rec["first_seen"]), rec.get("likes")]]
                rec["last_live"] = t0
                if c["likes"] != rec["likes"][-1][1]:
                    rec["likes"].append([int(t0), c["likes"]])
                rec["price_now"] = c["price"]
                seen_now[k] = rec
            elif not rebase and prev is not None and c["id"] > prev and not c["promoted"]:
                # Uploaded after the newest listing the previous sweep saw,
                # so after roughly that sweep - less the few minutes Vinted's
                # search index can lag, which the page's own upload time
                # corrects for every listing that gets checked.
                up_after = (prev_at or t0 - 3600) - 600
                if c["id"] <= clock_at(clock, up_after):
                    ev["old_by_clock"] += 1    # older than the claim: frontier was stale
                    continue
                watch[k] = dict(c, group=brand, first_seen=t0, up_after=up_after,
                                up_lo=up_after, up_hi=t0, last_live=t0,
                                likes=[[int(t0), c["likes"]]], checks=0)
                seen_now[k] = watch[k]
                ev["new"] += 1
        # Store the frontier this sweep actually used, not only a higher top.
        # When a unit's own frontier is stale the sweep starts from its
        # brand's, and a band whose newest listing is below that kept its
        # stale one while swept_at below was refreshed - so the next cycle
        # trusted a 15-day-old frontier as fresh (2026-11-21, 12:34 run).
        best = max([x for x in (top, prev) if x is not None], default=None)
        if best is not None:
            frontier[unit] = best
        if top is not None:
            cycle_top = max(cycle_top, top)
        st.setdefault("swept_at", {})[unit] = t0
        print(f"  {unit:<34} {len(got):>4} cards, floor {floor}, "
              f"{'complete' if complete else 'CUT SHORT'}", flush=True)
        if PACE.stop:
            break

    def covered(r: dict) -> bool:
        if not deep and r["first_seen"] < shallow_cut:
            return False                       # older than this cycle swept
        u = unit_of(r)
        return u in passed and int(r["id"]) >= passed[u]

    # Absent: watched, inside the span this cycle's sweep passed, not seen.
    missing = [k for k, r in watch.items() if k not in seen_now and covered(r)]
    for k in missing:
        watch[k]["misses"] = watch[k].get("misses", 0) + 1
    absent = [k for k in missing if watch[k]["misses"] >= MISSES_TO_CHECK
              and now - watch[k].get("checked_at", 0) >= RECHECK_H * 3600]
    ev["missing_once"] = sum(1 for k in missing if watch[k]["misses"] < MISSES_TO_CHECK)
    absent.sort(key=lambda k: watch[k]["first_seen"])
    ev["absent"] = len(absent)
    for k in absent:
        if PACE.stop:
            break
        state, det = check(s, watch[k])
        ev["checked"] += 1
        if state == "error" or state.startswith("http_"):
            continue                      # refused: try again next cycle
        rec = watch[k]
        rec.update({x: v for x, v in det.items() if v is not None})
        rec["checks"] += 1
        rec["checked_at"] = time.time()
        if not isinstance(rec.get("likes"), list):
            rec["likes"] = [[int(rec["first_seen"]), rec.get("likes")]]
        if det.get("likes_at_check") is not None:
            rec["likes"].append([int(rec["checked_at"]), det["likes_at_check"]])
        if det.get("upload_ago_h") is not None:
            narrow_upload(rec, det["upload_ago_h"], rec["checked_at"])
        if state in ("sold", "deleted", "gone"):
            closed.append(close(watch.pop(k), state, rec["checked_at"]))
        elif state == "reserved":
            rec["reserved_at"] = rec.get("reserved_at") or rec["checked_at"]
        if state in ("live", "reserved"):
            seen_now[k] = rec                  # confirmed alive by its own page

    # Retire at WATCH_H only listings this cycle confirmed alive - seen in a
    # full sweep or on their own page. One the sweep could not reach waits for
    # the next deep cycle rather than being closed "unsold" while perhaps sold.
    for k, r in list(watch.items()):
        age = now - r["first_seen"]
        alive = deep and k in seen_now
        if (age >= WATCH_H * 3600 and alive) or age >= HARD_END_H * 3600:
            watch.pop(k)
            end = ("reserved_at_end" if r.get("reserved_at") else "unsold") if alive else "unresolved"
            closed.append(close(r, end, now))

    for r in closed:
        ev["closed"][r["outcome"]] = ev["closed"].get(r["outcome"], 0) + 1
    ev["steals"] = sum(1 for r in closed if r.get("steal"))
    ev["requests"], ev["refused"], ev["watching"] = PACE.count, PACE.refused, len(watch)
    print(f"cycle: {ev}", flush=True)

    write_closed(closed)
    if cycle_top:
        st["id_clock"] = (clock + [[int(time.time()), cycle_top]])[-CLOCK_KEEP:]
    st["last_at"] = now
    st["cycles"] = (st.get("cycles") or [])[-HISTORY_KEEP:] + [ev]
    st["cycle_no"] = cycle_no + 1
    os.makedirs(DIR, exist_ok=True)
    json.dump(st, open(STATE, "w"), ensure_ascii=False, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
