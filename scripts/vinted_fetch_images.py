#!/usr/bin/env python3
"""Pull down the catalog photo for every listing the tracker has seen.

The tracker records an image URL but never fetches it, and that is the one
loss in this pipeline that cannot be undone. Everything else is recoverable:
prices, brands and sold state can be re-read or re-derived. An image URL that
stops resolving takes the photo with it, and for a listing that has already
sold or been deleted there is no page left to fetch a replacement from.

Vinted's URLs are signed (`?s=...`) and path-specific - only the /f800/ form
the signature was issued for resolves, so no smaller size can be requested.
They were verified good at 20h; beyond that is untested, which is precisely
why this should not wait.

Runs ON THE CLUSTER without the proxy, like archive_sold.py: the CDN serves
the university IP directly, so this costs no proxy credit. It only touches
images.vinted.net, never the site itself, so it cannot affect the tracker's
standing with the pages that actually rate-limit us.

Order matters. Listings that are already sold or deleted are fetched first,
because their URLs are the ones with no second chance; live tracked listings
can be re-discovered next cycle if a fetch is missed.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time

import requests

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(REPO, "data", "vinted_track.json")
OUT = "/extra/malmasik/vinted_images"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
GAP = (0.4, 1.0)          # CDN, not the site - but still sequential and paced
GIVE_UP = 15              # consecutive failures before stopping


def targets(st: dict) -> list[tuple[str, dict, str]]:
    """Every listing with an image url, most-at-risk first."""
    out, seen = [], set()
    # sold and archived listings have no live page behind them any more
    for bucket in ("sold", "archive"):
        for iid, rec in (st.get(bucket) or {}).items():
            if rec.get("img") and iid not in seen:
                seen.add(iid)
                out.append((iid, rec, bucket))
    for iid, rec in (st.get("tracked") or {}).items():
        if rec.get("img") and iid not in seen:
            seen.add(iid)
            out.append((iid, rec, "tracked"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=2000,
                    help="hard cap so a run can never become unbounded")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    try:
        st = json.load(open(STATE))
    except (OSError, ValueError) as exc:
        print(f"cannot read {STATE}: {exc}")
        return 1

    os.makedirs(OUT, exist_ok=True)
    todo = targets(st)
    have = {f.split(".")[0] for f in os.listdir(OUT) if f.endswith(".webp")}
    todo = [t for t in todo if t[0] not in have]

    by_bucket: dict[str, int] = {}
    for _, _, b in todo:
        by_bucket[b] = by_bucket.get(b, 0) + 1
    print(f"{len(have):,} already on disk, {len(todo):,} to fetch "
          f"{by_bucket}", flush=True)
    if a.dry_run:
        return 0

    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": "https://www.vinted.it/"})

    index_path = os.path.join(OUT, "_index.jsonl")
    index = open(index_path, "a")
    ok = fail = 0
    streak = 0
    started = time.time()

    for iid, rec, bucket in todo[:a.limit]:
        time.sleep(random.uniform(*GAP))
        try:
            r = s.get(rec["img"], timeout=45)
        except requests.RequestException as exc:
            print(f"  {iid}: {type(exc).__name__}", flush=True)
            fail += 1
            streak += 1
        else:
            if r.status_code == 200 and r.content:
                tmp = os.path.join(OUT, f".{iid}.tmp")
                with open(tmp, "wb") as fh:
                    fh.write(r.content)
                os.replace(tmp, os.path.join(OUT, f"{iid}.webp"))
                index.write(json.dumps({
                    "id": iid, "bucket": bucket,
                    "brand": rec.get("brand"), "price": rec.get("price"),
                    "slug": rec.get("slug"), "final": rec.get("final"),
                    "bytes": len(r.content), "at": int(time.time()),
                }) + "\n")
                index.flush()
                ok += 1
                streak = 0
            else:
                # A dead signature is the thing this script exists to beat, so
                # say so loudly rather than counting it as a generic failure.
                print(f"  {iid}: HTTP {r.status_code}"
                      f"{'  <-- url no longer resolves' if r.status_code in (403, 404) else ''}",
                      flush=True)
                fail += 1
                streak += 1

        if streak >= GIVE_UP:
            print(f"  {streak} failures in a row - stopping", flush=True)
            break
        if (ok + fail) % 250 == 0:
            print(f"  {ok:,} fetched, {fail} failed, "
                  f"{(ok+fail)/(time.time()-started):.1f}/s", flush=True)

    el = time.time() - started
    print(f"\n{ok:,} fetched, {fail} failed in {el/60:.1f} min", flush=True)
    remaining = len(todo) - ok
    if remaining > 0:
        print(f"{remaining:,} still to go - run again to continue", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
