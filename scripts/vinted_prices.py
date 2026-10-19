"""Asking-price reference: one row per brand x item type, with its filter link.

For each brand, walks Vinted's category tree and asks each category once,
newest first, searched by the brand's ids:

  - the reported total says how many live listings it holds (Vinted caps the
    figure at 960, so "960" means "960 or more")
  - the page's 96 newest listings are the price sample

Newest, not all, deliberately: the stock that has sat unsold is skewed toward
overpriced listings (what was cheap sold), so the newest uploads are closer to
what the market asks. This is still ASKING price. Sale prices come from the
sold-tracker (shoes) and the steals tracker (fast sales only, so they skew
cheap), and the table keeps the three apart rather than blending them.

The walk prunes: a category with no listings is not descended into. Rows sit
at the item-type level (Donna > Vestiti > Pantaloni, Donna > Accessori >
Guanti), split further into the leaves only where a type's count is capped
at 960 - there 96 newest listings are the thinnest sample of the whole. Kids
and Home stop at their first level.

Each brand's file is rewritten after every row, so a run cut short by its
time limit keeps what it reached. (The first run printed nothing until a
brand was done and wrote its file only at the end, and the 60-minute limit
cut Gucci off with nothing to show.)

Writes data/prices/<YYYY-MM-DD>/<brand>.json. Partitioned by brand so the
walk can be spread over several runs: --part i --parts n.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import statistics
import sys
import time

import requests

from vinted_steals import UA, Pacer, cards  # same parsing and pacing

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOGS = os.path.join(REPO, "data", "catalogs.json")
BRANDS = os.path.join(REPO, "data", "steals", "brands.json")
FULL = (1904, 5, 2993)      # Donna, Uomo, Articoli griffati: down to item type
SHALLOW = (1193, 1918)      # Bambini, Casa: first level only
SPLIT_AT = 960

PACE = Pacer()


def total(html: str) -> int | None:
    m = re.search(r'\\?"total_entries\\?":(\d+)', html)
    return int(m.group(1)) if m else None


def quantiles(xs: list[float]) -> dict:
    xs = sorted(x for x in xs if x)
    if not xs:
        return {}
    q = statistics.quantiles(xs, n=20, method="inclusive") if len(xs) > 1 else [xs[0]] * 19
    return {"n": len(xs), "min": xs[0], "p10": q[1], "p25": q[4], "p50": q[9],
            "p75": q[14], "p90": q[17], "max": xs[-1]}


def ask(s, cid: int, ids: list[int]) -> dict | None:
    params = [("catalog[]", cid)] + [("brand_ids[]", b) for b in ids] + [("order", "newest_first")]
    r = PACE.get(s, "https://www.vinted.it/catalog", params=params)
    if r is None or r.status_code != 200:
        return None
    cs = cards(r.text)
    n = total(r.text)
    if n is None:
        n = len(cs)
    # A card's condition is the last part of its second line ("M · Ottime").
    cond: dict[str, int] = {}
    for c in cs:
        k = (c.get("line2") or "").split("·")[-1].strip()
        if k:
            cond[k] = cond.get(k, 0) + 1
    print(f"    {cid:>5}  {n:>4} listings  ({PACE.count} requests)", flush=True)
    return {"listings": n, "capped": n >= 960, "asking": quantiles([c["price"] for c in cs]),
            "likes_median": statistics.median([c["likes"] or 0 for c in cs]) if cs else None,
            "conditions": cond}


def walk(s, brand: str, ids: list[int], nodes: dict, kids: dict, save=lambda rows: None) -> list[dict]:
    rows = []

    def row(cid, res, level):
        path, n = [], nodes.get(cid)
        while n:
            path.append(n["title"])
            n = nodes.get(n["parentId"])
        q = "".join(f"&brand_ids[]={b}" for b in ids)
        rows.append({"brand": brand, "catalog_id": cid, "path": " > ".join(path[::-1]),
                     "code": nodes[cid]["code"], "level": level,
                     "filter": f"https://www.vinted.it/catalog?catalog[]={cid}{q}&order=newest_first",
                     **res})
        print(f"  row  {rows[-1]['path']}", flush=True)
        save(rows)

    def visit(cid, depth, stop_depth):
        if PACE.stop:
            return
        res = ask(s, cid, ids)
        if not res or not res["listings"]:
            return
        children = kids.get(cid, [])
        if depth >= stop_depth or not children:
            row(cid, res, "type")
            if children and res["listings"] >= SPLIT_AT and stop_depth >= 2:
                for k in children:
                    sub = ask(s, k, ids) if not PACE.stop else None
                    if sub and sub["listings"]:
                        row(k, sub, "leaf")
            return
        if depth == 1:
            row(cid, res, "group")       # e.g. Donna > Scarpe, kept as a subtotal
        for k in children:
            visit(k, depth + 1, stop_depth)

    for root in FULL:
        visit(root, 0, 2)
    for root in SHALLOW:
        visit(root, 0, 1)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", type=int, default=0)
    ap.add_argument("--parts", type=int, default=1)
    a = ap.parse_args()
    nodes = {n["id"]: n for n in json.load(open(CATALOGS))}
    kids: dict[int, list[int]] = {}
    for n in sorted(nodes.values(), key=lambda n: n["order"]):
        kids.setdefault(n["parentId"], []).append(n["id"])
    groups = json.load(open(BRANDS))
    mine = [b for i, b in enumerate(groups) if i % a.parts == a.part]

    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
    s.get("https://www.vinted.it/", timeout=45)
    day = time.strftime("%Y-%m-%d", time.gmtime())
    out_dir = os.path.join(REPO, "data", "prices", day)
    os.makedirs(out_dir, exist_ok=True)
    for brand in mine:
        t0 = PACE.count
        path = os.path.join(out_dir, f"{re.sub(r'[^a-z]+', '-', brand.lower())}.json")

        def save(rows, done=False):
            tmp = path + ".tmp"
            json.dump({"brand": brand, "ids": groups[brand], "at": int(time.time()),
                       "complete": done, "requests": PACE.count - t0, "rows": rows},
                      open(tmp, "w"), ensure_ascii=False, indent=0)
            os.replace(tmp, path)

        print(f"{brand}:", flush=True)
        rows = walk(s, brand, groups[brand], nodes, kids, save)
        done = not PACE.stop
        save(rows, done)
        print(f"{brand}: {len(rows)} rows, {PACE.count - t0} requests, "
              f"{PACE.refused} refused, {'complete' if done else 'STOPPED'}", flush=True)
        if PACE.stop:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
