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
# Two ceilings, and the second one only showed up in production. Rate: 2.0s
# between requests is clean, 1.0s returns 21% HTTP 429. VOLUME: a 2,235-check
# run went clean for ~1,800 requests and was then 403'd for the remaining 448.
# So the cap sits well under that, and the clock budget usually binds first.
MAX_CHECK = int(os.environ.get("VT_MAX_CHECK", "1100"))
# The real limit is the clock, not the count: the Actions job is killed at 180
# minutes and a killed job never reaches the state save, losing the whole
# cycle. So checking stops with time to spare and whatever was not reached
# simply leads the queue next cycle - nothing is lost by stopping early.
BUDGET_MIN = float(os.environ.get("VT_BUDGET_MIN", "150"))
# Buckets for "how long had it been gone when we checked it". If deletions are
# really sales that 404'd first, sold% falls and deleted% rises across these.
AGE_BINS = [(0, 2, "<2h"), (2, 6, "2-6h"), (6, 12, "6-12h"),
            (12, 24, "12-24h"), (24, 1e9, ">24h")]
# Consecutive 403/429 before abandoning the cycle. A run that went 448
# refusals deep learned nothing after the first few.
BLOCK_GIVEUP = int(os.environ.get("VT_BLOCK_GIVEUP", "8"))
# Requests one job may make before stopping voluntarily.
#
# The wall is not sharp. Cycle 13 ran ~1,170 requests clean; cycle 14 was
# refused at roughly the same count; cycle 6 reached ~1,800 before refusal. So
# this sits under the lowest figure that has actually blocked, and jobs are
# sized to fill it rather than to leave it unused - a job that stops early
# wastes coverage, and one that overruns loses a cycle.
MAX_REQ = int(os.environ.get("VT_MAX_REQ", "1000"))
# Price bands swept per cycle per brand; 0 means all of them.
#
# Rotation put a hard ceiling on the whole dataset: sweeping 4 of ~6.2 bands
# means any given listing is only visible in ~65% of cycles, so 35% of items
# were never discovered no matter how long they stayed up. That ceiling
# dominated every other loss, including the sold/deleted ambiguity.
#
# A full sweep measured ~327 requests (4 bands took ~14 min / ~211 requests),
# not the ~600 feared - the earlier attempt blocked because it ran with the
# OLD oversized bands and a 10-page depth. With bands resized to <=593 and
# checks capped at the level proven clean, the whole cycle is ~1,400 requests,
# which cycles 11 and 13 both sustained without a single 403.
BANDS_PER_CYCLE = int(os.environ.get("VT_BANDS_PER_CYCLE", "0"))
# Stop tracking listings older than this. Vinted publishes no absolute date, so
# age is only knowable from the item page - which means a listing is aged out
# when it is next checked, not in a single sweep. Aged-out rows are moved to
# the archive, never discarded: the photo, price and brand are still dataset,
# it is only the sale-watching that stops.
AGE_LIMIT_DAYS = float(os.environ.get("VT_AGE_LIMIT_DAYS", "180"))
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
        self.streak = 0          # consecutive blocks
        self.count = 0           # requests made by this job

    @property
    def blocked(self) -> bool:
        """Give up for this cycle once it is clearly not a transient refusal.

        Once the cumulative ceiling is hit, every further request is refused;
        continuing would spend the whole budget learning that repeatedly, and
        would keep hammering a host that has already said no. Whatever is not
        checked simply leads the queue next cycle.
        """
        return self.streak >= BLOCK_GIVEUP

    @property
    def spent(self) -> bool:
        """This job has used its share of the per-IP allowance."""
        return self.count >= MAX_REQ

    def wait(self) -> None:
        self.count += 1
        time.sleep(random.uniform(self.gap, self.gap * 1.4))

    def saw(self, status: int | None) -> None:
        # 403 counts as a block, not an error. A 2,235-check run stayed clean
        # for ~1,800 requests and then returned 448 consecutive 403s - a
        # CUMULATIVE volume ceiling, which no fixed gap avoids and which a
        # short rate probe cannot see. Because only 429 was handled, the run
        # sailed through every one of them without slowing down.
        if status in (429, 403):
            self.throttled += 1
            self.ok_streak = 0
            self.streak += 1
            self.gap = min(GAP_CEIL, self.gap * 1.8)
            print(f"    HTTP {status} - slowing to {self.gap:.1f}s "
                  f"({self.streak} in a row)", flush=True)
            time.sleep(30)
        elif status == 200:
            self.ok_streak += 1
            self.streak = 0
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


def covered(rec: dict, swept: dict) -> bool:
    """Was this listing inside a price band we actually searched this cycle?

    Absence only means something within a span that was searched. A listing
    priced EUR 300 is not "gone" because we only swept the 0-100 band - it was
    never looked for. Treating those as absent floods the check queue with
    listings that are plainly alive: one cycle marked 6,382 absent, of which
    98.2% came back live.
    """
    spans = swept.get(rec.get("brand"))
    if not spans:
        return False
    if spans == [(None, None)]:          # unbanded sweep covers everything
        return True
    try:
        p = float(rec.get("price") or 0)
    except (TypeError, ValueError):
        return False
    if p <= 0:
        return False
    return any(lo <= p <= hi for lo, hi in spans)


def sweep(s, brand, bands=None, cycle=0):
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
    # Sweeping every band every hour would spend ~600 requests on discovery,
    # and those come out of the SAME per-IP ceiling that the state checks do -
    # so full band coverage each cycle would starve the checks that actually
    # find sales. Bands are rotated instead: a price band does not turn over
    # much in an hour, and every band is still visited within a few cycles.
    if bands and 0 < BANDS_PER_CYCLE < len(spans):
        start = (cycle * BANDS_PER_CYCLE) % len(spans)
        spans = [spans[(start + k) % len(spans)] for k in range(BANDS_PER_CYCLE)]
    # Only spans that actually came back count as swept. Cycle 9 recorded
    # every planned span, was then blocked partway, and two brands returned
    # nothing at all - so 10,506 listings were marked "absent from the feed"
    # when the feed had simply never been read. A failed sweep must look like
    # no sweep, not like an empty one.
    done = []
    for lo, hi in spans:
        got_any = False
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
            got_any = True
            n, fresh = _parse(r.text, found)
            # Stop as soon as a page adds nothing new: a band shallower than
            # PAGES otherwise burns a full page request per empty page, and
            # with ten brands times six bands that waste is the whole budget.
            if n == 0 or fresh == 0:
                break
        if got_any:
            done.append((lo, hi))
        if PACE.blocked or PACE.spent:
            why = "blocked" if PACE.blocked else "request budget"
            print(f"    {brand}: {why}, {len(spans)-len(done)} bands unswept",
                  flush=True)
            break
    return found, done


UNITS_IT = {"minut": 1 / 1440, "ora": 1 / 24, "ore": 1 / 24, "giorn": 1,
            "settiman": 7, "mes": 30.4, "ann": 365}
# Anchored on Vinted's own field, NOT on a bare "N giorni fa" match. Every item
# page also carries "più di 90 giorni fa" in wallet boilerplate and "Ultima
# visita N ore fa" for the seller's last login; pruning on either would age out
# the whole corpus at once.
UPLOAD_RE = re.compile(
    r'\\?"code\\?":\\?"upload_date\\?".{0,120}?\\?"value\\?":\\?"([^"\\]{2,40})')


def parse_age_days(text: str) -> float | None:
    """Days since upload, from Vinted's Italian relative date."""
    m = UPLOAD_RE.search(text)
    if not m:
        return None
    v = m.group(1).strip().lower()
    num = re.search(r"(\d+)", v)
    # "un mese fa" / "una settimana fa" carry no digit but mean one.
    n = int(num.group(1)) if num else 1
    for stem, days in UNITS_IT.items():
        if stem in v:
            return round(n * days, 2)
    return None


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
           "availability": av.group(1) if av else None,
           # Free: we already have the page. Vinted publishes no absolute date
           # anywhere, so this relative string is the only way to know a
           # listing's age at all.
           "age_days": parse_age_days(t)}
    if can is None:
        return "unknown", det
    if can.group(1) == "true":
        return "live", det
    return ("reserved" if det["reserved"] else "sold"), det


def load_state() -> dict:
    try:
        st = json.load(open(STATE))
    except (OSError, ValueError):
        st = {"cycle": 0, "tracked": {}, "sold": {}, "events": []}
    # Every listing ever discovered, with the state it ended in. This is the
    # dataset; tracked/ is only the working set.
    st.setdefault("archive", {})
    return st


def save_state(st: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"), separators=(",", ":"))
    os.replace(tmp, STATE)


def session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
    s.get("https://www.vinted.it/", timeout=45)
    return s


def load_bands() -> dict:
    if os.environ.get("VT_BANDS", "1") == "0":
        return {}
    try:
        return json.load(open(os.path.join(REPO, "data", "vinted_bands.json")))
    except (OSError, ValueError):
        print("  no band file - falling back to unbanded search", flush=True)
        return {}


# --------------------------------------------------------------- shard modes
#
# One job doing everything cannot be split, because it reads and writes state
# in the same process. These four modes separate the work that can run in
# parallel (sweeping, checking - neither touches state) from the work that
# must not (planning, applying - single writer, so shards never conflict).


def mode_discover(shard: int, shards: int, out: str) -> int:
    """Sweep this shard's brands. Touches no state, so shards cannot collide."""
    mine = [b for i, b in enumerate(BRANDS) if i % shards == shard]
    print(f"discover shard {shard}/{shards}: {mine}", flush=True)
    s, bands, found = session(), load_bands(), {}
    # Band rotation needs the cycle number; plan/ owns incrementing it, so read
    # the current value rather than advancing it here.
    cycle = load_state().get("cycle", 0) + 1
    swept = {}
    for b in mine:
        f, spans = sweep(s, b, bands.get(b), cycle)
        swept[b] = spans
        for iid, v in f.items():
            v["brand"] = b
            found[iid] = v
        print(f"  {b:<20} {len(f):>5} listings"
              f"{'' if b in bands else '  (unbanded)'}", flush=True)
    # The swept spans travel with the listings so plan/ can tell a genuine
    # disappearance from a band that was simply not searched this cycle.
    json.dump({"found": found, "swept": swept}, open(out, "w"),
              separators=(",", ":"))
    print(f"shard {shard}: {len(found):,} listings in {PACE.count} requests "
          f"-> {out}", flush=True)
    return 0


def mode_plan(indir: str, out: str) -> int:
    """Merge shard discoveries into state and decide what to check."""
    st, now = load_state(), int(time.time())
    st["cycle"] += 1
    seen_now, swept = {}, {}
    for fn in sorted(os.listdir(indir)):
        if fn.endswith(".json"):
            d = json.load(open(os.path.join(indir, fn)))
            seen_now.update(d.get("found", {}))
            swept.update(d.get("swept", {}))
    print(f"total distinct discovered: {len(seen_now):,}", flush=True)

    absent_set = {i for i in st["tracked"]
                  if i not in seen_now and covered(st["tracked"][i], swept)}
    due = [i for i in st["tracked"]
           if i in absent_set
           or now - st["tracked"][i].get("last_check", 0) > RECHECK_H * 3600]
    due.sort(key=lambda i: (0 if i in absent_set else 1,
                            st["tracked"][i].get("last_check", 0)))
    # How long a listing has been missing from the feed, carried into the
    # queue. Deletions run ~1:1 with sales, and the suspicion is that many are
    # sales that 404'd before we arrived - if so, sold-rate should FALL and
    # deleted-rate RISE with absence age, which is what makes faster checking
    # worth paying for. That is measurable, so measure it.
    queue = [{"id": i, "slug": st["tracked"][i].get("slug", ""),
              "absent": i in absent_set,
              "gone_h": round((now - st["tracked"][i].get("last_seen", now))
                              / 3600, 1) if i in absent_set else 0.0}
             for i in due[:MAX_CHECK]]

    for iid, v in seen_now.items():
        if iid in st["tracked"]:
            st["tracked"][iid]["last_seen"] = now
        elif iid not in st["sold"]:
            v["first_seen"] = now
            v["last_seen"] = now
            v["last_check"] = 0
            st["tracked"][iid] = v

    st["pending"] = {"at": now, "seen": len(seen_now),
                     "absent": len(absent_set), "due": len(due)}
    save_state(st)
    json.dump(queue, open(out, "w"), separators=(",", ":"))
    print(f"tracking {len(st['tracked']):,}, absent {len(absent_set):,}, "
          f"due {len(due):,}, queued {len(queue):,}", flush=True)
    return 0


def mode_check(queue_file: str, shard: int, shards: int, out: str) -> int:
    """Check this shard's slice. Writes verdicts only - never state."""
    q = json.load(open(queue_file))
    mine = [x for i, x in enumerate(q) if i % shards == shard]
    print(f"check shard {shard}/{shards}: {len(mine)} listings", flush=True)
    s = session()
    started, deadline = time.time(), time.time() + BUDGET_MIN * 60
    res = {}
    for n, item in enumerate(mine):
        if time.time() > deadline:
            print(f"  budget reached - {len(mine) - n} deferred", flush=True)
            break
        if PACE.blocked or PACE.spent:
            why = f"blocked after {PACE.throttled} refusals" \
                if PACE.blocked else f"request budget ({PACE.count})"
            print(f"  {why} - {len(mine) - n} deferred", flush=True)
            break
        v, det = check_state(s, item["id"], item["slug"])
        res[item["id"]] = {"v": v, "absent": item["absent"],
                           "gone_h": item.get("gone_h", 0.0),
                           "age_days": det.get("age_days"),
                           "availability": det.get("availability")}
    json.dump(res, open(out, "w"), separators=(",", ":"))
    print(f"shard {shard}: {len(res)} checked in "
          f"{(time.time()-started)/60:.0f} min, {PACE.count} requests, "
          f"final gap {PACE.gap:.1f}s, {PACE.throttled} throttled", flush=True)
    return 0


def mode_apply(indir: str) -> int:
    """Fold every shard's verdicts into state. Single writer again."""
    st, now = load_state(), int(time.time())
    verdicts, split = {}, {"absent": {}, "rotation": {}}
    age_buckets: dict[str, dict] = {}
    for fn in sorted(os.listdir(indir)):
        if not fn.endswith(".json"):
            continue
        for iid, r in json.load(open(os.path.join(indir, fn))).items():
            v = r["v"]
            verdicts[v] = verdicts.get(v, 0) + 1
            o = "absent" if r.get("absent") else "rotation"
            split[o][v] = split[o].get(v, 0) + 1
            if r.get("absent"):
                g = r.get("gone_h", 0.0)
                for lo, hi, lab in AGE_BINS:
                    if lo <= g < hi:
                        age_buckets.setdefault(lab, {})
                        age_buckets[lab][v] = age_buckets[lab].get(v, 0) + 1
                        break
            rec = st["tracked"].get(iid)
            if rec is None:
                continue
            rec["last_check"] = now
            if v == "sold":
                st["sold"][iid] = {**rec, "sold_seen": now,
                                   "availability": r.get("availability"),
                                   "was_absent": bool(r.get("absent"))}
                st["archive"][iid] = {**rec, "final": "sold", "at": now}
                st["tracked"].pop(iid, None)
            elif v == "deleted":
                # A 404 is not nothing. Deletions run about 1:1 with confirmed
                # sales, and a listing that sells and is then removed by the
                # seller 404s before we reach it - so an unknown share of these
                # ARE sales. Either way the brand, price and photo were already
                # captured at discovery, and discarding them threw away usable
                # dataset rows to save nothing.
                st["archive"][iid] = {**rec, "final": "deleted", "at": now}
                st["tracked"].pop(iid, None)
            elif (r.get("age_days") or 0) > AGE_LIMIT_DAYS:
                st["archive"][iid] = {**rec, "final": "aged_out", "at": now,
                                      "age_days": r["age_days"]}
                st["tracked"].pop(iid, None)
            elif r.get("age_days") is not None:
                rec["age_days"] = r["age_days"]

    p = st.pop("pending", {})
    st["events"].append({"at": now, "cycle": st["cycle"], "sharded": True,
                         "seen": p.get("seen"), "absent": p.get("absent"),
                         "checked": sum(verdicts.values()),
                         "verdicts": verdicts, "split": split})
    save_state(st)
    print("verdicts:", verdicts, flush=True)
    for o in ("absent", "rotation"):
        n = sum(split[o].values())
        if n:
            sold = split[o].get("sold", 0)
            print(f"  {o:<9} {n:>5} checked -> {sold} sold "
                  f"({100*sold/n:.1f}%)  {split[o]}", flush=True)
    if age_buckets:
        print("\nverdict by how long the listing had been gone:", flush=True)
        for lo, hi, lab in AGE_BINS:
            b = age_buckets.get(lab)
            if not b:
                continue
            n = sum(b.values())
            print(f"  gone {lab:<8} n={n:>5}  "
                  f"sold {100*b.get('sold',0)/n:>5.1f}%  "
                  f"deleted {100*b.get('deleted',0)/n:>5.1f}%  "
                  f"live {100*b.get('live',0)/n:>5.1f}%", flush=True)
    print(f"cycle {st['cycle']}: tracking {len(st['tracked']):,}, "
          f"confirmed sold so far {len(st['sold']):,}", flush=True)
    return 0


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["discover", "plan", "check", "apply"])
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--queue")
    ap.add_argument("--in", dest="indir")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.mode == "discover":
        return mode_discover(a.shard, a.shards, a.out)
    if a.mode == "plan":
        return mode_plan(a.indir, a.out)
    if a.mode == "check":
        return mode_check(a.queue, a.shard, a.shards, a.out)
    if a.mode == "apply":
        return mode_apply(a.indir)
    return main_single()


def main_single() -> int:
    """The original one-job cycle, kept working for manual runs."""
    st = load_state()
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
    swept: dict[str, list] = {}
    for b in BRANDS:
        f, spans = sweep(s, b, bands.get(b), st["cycle"])
        swept[b] = spans
        for iid, v in f.items():
            v["brand"] = b
            seen_now[iid] = v
        print(f"  {b:<20} {len(f):>5} listings"
              f"{'' if b in bands else '  (unbanded)'}", flush=True)
    print(f"total distinct: {len(seen_now):,}", flush=True)

    absent = [i for i in st["tracked"]
              if i not in seen_now and covered(st["tracked"][i], swept)]
    uncovered = len(st["tracked"]) - len(seen_now & st["tracked"].keys()) \
        - len(absent)
    print(f"\ntracked {len(st['tracked']):,}, absent from a band we actually "
          f"swept {len(absent):,} ({uncovered:,} in unswept bands, not absent)",
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
    age_buckets: dict[str, dict] = {}
    aged = 0
    deadline = started + BUDGET_MIN * 60
    stopped_early = 0
    for n_done, iid in enumerate(batch):
        if time.time() > deadline:
            stopped_early = len(batch) - n_done
            print(f"  budget reached - {stopped_early} checks deferred to "
                  f"next cycle", flush=True)
            break
        if PACE.blocked:
            stopped_early = len(batch) - n_done
            print(f"  blocked after {PACE.throttled} refusals - "
                  f"{stopped_early} deferred to next cycle", flush=True)
            break
        rec = st["tracked"][iid]
        v, det = check_state(s, iid, rec.get("slug", ""))
        verdicts[v] = verdicts.get(v, 0) + 1
        origin = "absent" if iid in absent_set else "rotation"
        split[origin][v] = split[origin].get(v, 0) + 1
        if origin == "absent":
            gone_h = (now - rec.get("last_seen", now)) / 3600
            for lo, hi, lab in AGE_BINS:
                if lo <= gone_h < hi:
                    age_buckets.setdefault(lab, {})
                    age_buckets[lab][v] = age_buckets[lab].get(v, 0) + 1
                    break
        rec["last_check"] = now
        if v == "sold":
            st["sold"][iid] = {**rec, "sold_seen": now,
                               "availability": det.get("availability"),
                               "was_absent": iid in absent_set}
            st["archive"][iid] = {**rec, "final": "sold", "at": now}
            st["tracked"].pop(iid, None)          # terminal
        elif v == "deleted":
            # Keep the row: deletions run ~1:1 with sales and an unknown share
            # of them ARE sales that 404'd before we got there.
            st["archive"][iid] = {**rec, "final": "deleted", "at": now}
            st["tracked"].pop(iid, None)          # terminal
        elif (det.get("age_days") or 0) > AGE_LIMIT_DAYS:
            aged += 1
            st["archive"][iid] = {**rec, "final": "aged_out", "at": now,
                                  "age_days": det["age_days"]}
            st["tracked"].pop(iid, None)
        elif det.get("age_days") is not None:
            rec["age_days"] = det["age_days"]
    if aged:
        print(f"  aged out (>{AGE_LIMIT_DAYS:.0f}d since upload): {aged}",
              flush=True)
    if verdicts:
        print("  verdicts:", verdicts, flush=True)
        for o in ("absent", "rotation"):
            n = sum(split[o].values())
            if n:
                sold = split[o].get("sold", 0)
                print(f"    {o:<9} {n:>4} checked -> {sold} sold "
                      f"({100*sold/n:.1f}%)  {split[o]}", flush=True)
        if age_buckets:
            print("  verdict by how long the listing had been gone:", flush=True)
            for lo, hi, lab in AGE_BINS:
                b = age_buckets.get(lab)
                if not b:
                    continue
                n = sum(b.values())
                print(f"    gone {lab:<8} n={n:>5}  "
                      f"sold {100*b.get('sold',0)/n:>5.1f}%  "
                      f"deleted {100*b.get('deleted',0)/n:>5.1f}%  "
                      f"live {100*b.get('live',0)/n:>5.1f}%", flush=True)

    for iid, v in seen_now.items():
        if iid in st["tracked"]:
            # Refresh last_seen so absence age stays meaningful: without this
            # every listing looks like it has been gone since first discovery.
            st["tracked"][iid]["last_seen"] = now
        elif iid not in st["sold"]:
            v["first_seen"] = now
            v["last_seen"] = now
            v["last_check"] = 0
            st["tracked"][iid] = v

    st["events"].append({"at": now, "cycle": st["cycle"],
                         "seen": len(seen_now), "absent": len(absent),
                         "checked": len(batch), "verdicts": verdicts,
                         "split": split, "gap": round(PACE.gap, 2),
                         "throttled": PACE.throttled,
                         "deferred": stopped_early, "age": age_buckets,
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
