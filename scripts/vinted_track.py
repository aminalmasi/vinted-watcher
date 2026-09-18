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
PAGES = int(os.environ.get("VT_PAGES", "3"))
GAP = (3.0, 6.0)
MAX_CHECK = int(os.environ.get("VT_MAX_CHECK", "60"))


def get(s, url, **kw):
    time.sleep(random.uniform(*GAP))
    try:
        return s.get(url, timeout=60, **kw)
    except requests.RequestException as e:
        print(f"    {type(e).__name__}", flush=True)
        return None


def sweep(s, brand):
    """One brand's current listings: id -> price, image, title."""
    found = {}
    for page in range(1, PAGES + 1):
        r = get(s, "https://www.vinted.it/catalog",
                params={"search_text": f"{brand} shoes", "page": page})
        if r is None or r.status_code != 200:
            break
        t = r.text
        ids = re.findall(r"/items/(\d{6,12})-([a-z0-9-]{3,60})", t)
        prices = re.findall(r'\\?"id\\?":(\d{6,12}),.{0,400}?\\?"amount\\?":\\?"([\d.]+)', t)
        pmap = dict(prices)
        imgs = dict(re.findall(
            r'\\?"id\\?":(\d{6,12}),.{0,3000}?(https://images\d*\.vinted\.net/[^"\\\s]+/f800/[^"\\\s]+)', t))
        for iid, slug in ids:
            if iid not in found:
                found[iid] = {"slug": slug, "price": pmap.get(iid),
                              "img": imgs.get(iid)}
        if len(ids) == 0:
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
    now = int(time.time())
    st["cycle"] += 1
    first_cycle = st["cycle"] == 1

    seen_now = {}
    for b in BRANDS:
        f = sweep(s, b)
        for iid, v in f.items():
            v["brand"] = b
            seen_now[iid] = v
        print(f"  {b:<20} {len(f):>4} listings", flush=True)
    print(f"total distinct: {len(seen_now):,}", flush=True)

    gone = [i for i in st["tracked"] if i not in seen_now]
    print(f"\ntracked previously {len(st['tracked']):,}, "
          f"no longer in feed: {len(gone):,}", flush=True)

    verdicts = {}
    if not first_cycle and gone:
        random.shuffle(gone)
        for iid in gone[:MAX_CHECK]:
            rec = st["tracked"][iid]
            v, det = check_state(s, iid, rec.get("slug", ""))
            verdicts[v] = verdicts.get(v, 0) + 1
            if v == "sold":
                st["sold"][iid] = {**rec, "sold_seen": now,
                                   "availability": det.get("availability")}
        print("  verdicts:", verdicts, flush=True)

    for iid, v in seen_now.items():
        if iid not in st["tracked"]:
            v["first_seen"] = now
            st["tracked"][iid] = v
    for iid in gone[:MAX_CHECK]:
        st["tracked"].pop(iid, None)

    st["events"].append({"at": now, "cycle": st["cycle"],
                         "seen": len(seen_now), "gone": len(gone),
                         "verdicts": verdicts})
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(st, open(STATE, "w"), separators=(",", ":"))
    print(f"\ncycle {st['cycle']}: tracking {len(st['tracked']):,}, "
          f"confirmed sold so far {len(st['sold']):,}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
