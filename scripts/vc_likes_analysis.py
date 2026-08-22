"""Do likes predict a sale? Reads the cohort dataset; touches no network.

Runs anywhere, cluster included — it only reads data/vc_cohort.jsonl.

Method: replay the event log per listing to recover its like history and its
outcome, then bucket by peak likes and compare sale rates. Two honest habits
built in:

  * `gone` listings (vanished with no recorded sale) are reported SEPARATELY
    and excluded from the rate, not silently counted as "did not sell" — some
    of them almost certainly did sell.
  * items still live are censored, not failures. A shoe listed two days ago has
    not "failed to sell"; it has not finished being observed. Sale rates are
    computed only over listings whose outcome is known.
"""

from __future__ import annotations

import json
import os
import sys
from statistics import median

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data", "vc_cohort.jsonl")
BUCKETS = [(0, 0), (1, 2), (3, 5), (6, 10), (11, 20), (21, 50), (51, 10**9)]


def label(lo, hi):
    return f"{lo}" if lo == hi else (f"{lo}+" if hi > 10**8 else f"{lo}-{hi}")


def main() -> int:
    if not os.path.exists(DATA):
        print("No dataset yet — the cohort job has not run.")
        return 0
    items: dict[str, dict] = {}
    for line in open(DATA):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        it = items.setdefault(e["id"], {"likes": [], "out": None, "days": None,
                                        "brand": None, "price": None})
        if e["e"] == "new":
            it["brand"], it["price"] = e.get("b"), e.get("p")
        if e.get("l") is not None:
            it["likes"].append(e["l"])
        if e["e"] in ("sold", "gone"):
            it["out"] = e["e"]
            it["days"] = e.get("d")

    n = len(items)
    sold = [i for i in items.values() if i["out"] == "sold"]
    gone = [i for i in items.values() if i["out"] == "gone"]
    live = [i for i in items.values() if i["out"] is None]
    print(f"dataset: {n} listings — {len(sold)} sold, {len(gone)} vanished "
          f"(outcome unknown), {len(live)} still live (censored)\n")

    known = [i for i in items.values() if i["out"] == "sold" or i["out"] is None]
    if len(sold) < 20:
        print(f"Only {len(sold)} confirmed sales so far — too few to read "
              f"anything into. Let it collect for a week or so.")
        return 0

    print(f"{'peak likes':>12} {'n':>6} {'sold':>6} {'rate':>7} {'median days':>12}")
    for lo, hi in BUCKETS:
        grp = [i for i in known if i["likes"] and lo <= max(i["likes"]) <= hi]
        if not grp:
            continue
        s = [i for i in grp if i["out"] == "sold"]
        ds = [i["days"] for i in s if i.get("days") is not None]
        rate = f"{100*len(s)/len(grp):.0f}%"
        print(f"{label(lo,hi):>12} {len(grp):>6} {len(s):>6} {rate:>7} "
              f"{(f'{median(ds):.1f}' if ds else '—'):>12}")

    if gone:
        print(f"\n{len(gone)} listings vanished without a recorded sale and are "
              f"excluded above; if most of those were really sales, the rates "
              f"are understated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
