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
RECHECK_H = 3.0

# Brands with more than ~960 uploads a day are swept in category groups:
# one search shows at most 960 listings, and Gucci, Dior, Prada, Saint
# Laurent and Valentino passed that within their first day, so their sweeps
# were cut short and their older listings could never be seen to go missing.
# A search takes several categories at once (probed: it returns their
# union), so six searches partition a brand. Category filters leak a little
# (women's and men's gloves share listings), which only causes duplicates,
# and a listing is counted once.
SPLIT = [b.strip() for b in os.environ.get(
    "VS_SPLIT", "Gucci,Chanel,Dior,Prada,Saint Laurent,Valentino").split(",") if b.strip()]
CAT_GROUPS = [("women-clothing", [4]), ("women-shoes", [16]), ("women-bags", [19]),
              ("women-accessories", [1187, 146]), ("men", [5]),
              ("other", [2993, 1193, 1918, 2994, 2309, 4824, 4332])]

# Most cycles sweep only the newest SHALLOW_H hours, where nearly all steals
# happen (median time to sale 1.2h; 10% of sales come after 12h); every
# DEEP_EVERY-th cycle sweeps the whole watch window, and only those retire
# listings at WATCH_H, so a listing is never closed "unsold" without a sweep
# having seen it alive.
SHALLOW_H = float(os.environ.get("VS_SHALLOW_H", "12"))
DEEP_EVERY = int(os.environ.get("VS_DEEP_EVERY", "4"))
HARD_END_H = WATCH_H + 6           # closed regardless, so state stays bounded

# A cycle that runs out of time is cancelled by Actions and saves NOTHING -
# three in a row were lost that way. So the cycle stops itself first.
BUDGET_S = float(os.environ.get("VS_BUDGET_MIN", "21")) * 60
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

def sweep(s, ids: list[int], cats: list[int] | None,
          stop_below: int | None) -> tuple[list[dict], int | None, bool]:
    """Newest-first pages for one brand (or one category group of it) until a
    whole page is older than the oldest listing still watched.

    Returns the cards, the lowest id the sweep is known to have passed, and
    whether it got there. The first version stopped at the page whose MEDIAN
    id was old enough; newest-first is not strictly id order, so listings
    near that edge sat on the next, unfetched page and were counted absent -
    half of all checks found them still live. One more page per sweep is far
    cheaper than those checks.
    """
    got: list[dict] = []
    params = [("catalog[]", c) for c in (cats or [])] + \
        [("brand_ids[]", b) for b in ids] + [("order", "newest_first")]
    last_max = None
    for page in range(1, PAGES + 1):
        if PACE.stop:
            return got, last_max, False
        r = PACE.get(s, "https://www.vinted.it/catalog", params=params + [("page", page)])
        if r is None or r.status_code != 200:
            return got, last_max, False
        cs = cards(r.text)
        got.extend(cs)
        organic = [c["id"] for c in cs if not c["promoted"]] or [c["id"] for c in cs]
        if len(organic) < 48:              # the feed ran out: everything was shown
            return got, 0, True
        last_max = max(organic)
        if stop_below is None or last_max < stop_below:
            return got, (stop_below if stop_below is not None else min(organic)), True
    return got, last_max, False


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
        rec["steal"] = rec["sold_h_est"] <= STEAL_H
    return rec


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
    global T_START
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

    cycle_no = len(st.get("cycles") or [])
    deep = cycle_no % DEEP_EVERY == 0
    ev["deep"] = deep
    shallow_cut = now - SHALLOW_H * 3600

    # Sweep units: a whole brand, or one category group of a split brand.
    units = []
    for brand, ids in groups.items():
        if brand in SPLIT:
            units += [(f"{brand}|{g}", brand, ids, cats) for g, cats in CAT_GROUPS]
        else:
            units.append((brand, brand, ids, None))

    seen_now: dict[str, dict] = {}
    passed: dict[str, int] = {}
    for unit, brand, ids, cats in units:
        # A record belongs to the unit that first showed it; records from
        # before the split (no unit yet) count toward every unit of their brand.
        mine = [r for k, r in watch.items() if r["group"] == brand
                and r.get("unit", unit) == unit and (deep or r["first_seen"] >= shallow_cut)]
        prev_at = (st.get("swept_at") or {}).get(unit) or (st.get("swept_at") or {}).get(brand)
        prev = frontier.get(unit) or frontier.get(brand)
        stop_below = min(int(r["id"]) for r in mine) if mine else prev
        t0 = time.time()
        got, floor, complete = sweep(s, ids, cats, stop_below)
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
                rec.setdefault("unit", unit)
                if not isinstance(rec.get("likes"), list):     # repair the first version's overwrite
                    rec["likes"] = [[int(rec["first_seen"]), rec.get("likes")]]
                rec["last_live"] = t0
                if c["likes"] != rec["likes"][-1][1]:
                    rec["likes"].append([int(t0), c["likes"]])
                rec["price_now"] = c["price"]
                seen_now[k] = rec
            elif prev is not None and c["id"] > prev and not c["promoted"]:
                # Uploaded after the newest listing the previous sweep saw,
                # so after roughly that sweep - less the few minutes Vinted's
                # search index can lag, which the page's own upload time
                # corrects for every listing that gets checked.
                up_after = (prev_at or t0 - 3600) - 600
                watch[k] = dict(c, group=brand, unit=unit, first_seen=t0, up_after=up_after,
                                up_lo=up_after, up_hi=t0, last_live=t0,
                                likes=[[int(t0), c["likes"]]], checks=0)
                seen_now[k] = watch[k]
                ev["new"] += 1
        if top is not None and (prev is None or top > prev):
            frontier[unit] = top
        st.setdefault("swept_at", {})[unit] = t0
        print(f"  {unit:<34} {len(got):>4} cards, floor {floor}, "
              f"{'complete' if complete else 'CUT SHORT'}", flush=True)
        if PACE.stop:
            break

    def covered(r: dict) -> bool:
        if not deep and r["first_seen"] < shallow_cut:
            return False                       # older than this cycle swept
        if r.get("unit"):
            return r["unit"] in passed and int(r["id"]) >= passed[r["unit"]]
        brand_units = [u for u, b, _, _ in units if b == r["group"]]
        return all(u in passed and int(r["id"]) >= passed[u] for u in brand_units)

    # Absent: watched, inside the span this cycle's sweep passed, not seen.
    absent = [k for k, r in watch.items()
              if k not in seen_now and covered(r)
              and now - r.get("checked_at", 0) >= RECHECK_H * 3600]
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
    st["last_at"] = now
    st["cycles"] = (st.get("cycles") or [])[-500:] + [ev]
    os.makedirs(DIR, exist_ok=True)
    json.dump(st, open(STATE, "w"), ensure_ascii=False, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
