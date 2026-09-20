#!/usr/bin/env python3
"""Merge this run's state with whatever is already on origin/main.

Two cycles overlapped overnight and both wrote cycle 9. Each had read the same
starting state, worked for an hour, then written the WHOLE state file - so the
one that finished later silently overwrote the other's results and nine
confirmed sales disappeared:

    00:14  sold=43  tracked=17,485
    00:54  sold=34  tracked=19,278   <- 9 sales lost

A confirmed sale is the one thing in this pipeline that cannot be recovered:
the listing is gone, and nothing will ever report it again. So persistence has
to merge rather than replace.

The merge rules follow from what each field means:

  sold, archive   UNION, ours never wins a deletion. These are append-only
                  records of things that happened; if either side saw a sale,
                  it happened.
  tracked         ours, minus anything either side has since retired. This is
                  a working set, and ours is the fresher view of the feed.
  cycle           max, so the counter never goes backwards.
  events          concatenated and de-duplicated on (cycle, at).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(REPO, "data", "vinted_track.json")
REMOTE = "origin/main:data/vinted_track.json"


def remote_state() -> dict | None:
    try:
        subprocess.run(["git", "fetch", "-q", "origin", "main"],
                       cwd=REPO, check=True, timeout=120)
        out = subprocess.run(["git", "show", REMOTE], cwd=REPO,
                             capture_output=True, check=True, timeout=120)
        return json.loads(out.stdout)
    except (subprocess.SubprocessError, ValueError) as exc:
        print(f"  no usable remote state ({type(exc).__name__}) - keeping ours",
              flush=True)
        return None


def main() -> int:
    ours = json.load(open(STATE))
    theirs = remote_state()
    if theirs is None:
        return 0

    before = len(ours.get("sold", {}))

    # Append-only records: a sale either side saw is a sale.
    sold = {**theirs.get("sold", {}), **ours.get("sold", {})}
    archive = {**theirs.get("archive", {}), **ours.get("archive", {})}

    # Working set: ours is the fresher view, but anything EITHER side has
    # retired must not come back to life.
    retired = set(sold) | set(archive)
    tracked = {k: v for k, v in ours.get("tracked", {}).items()
               if k not in retired}

    seen, events = set(), []
    for e in (theirs.get("events", []) + ours.get("events", [])):
        key = (e.get("cycle"), e.get("at"))
        if key not in seen:
            seen.add(key)
            events.append(e)
    events.sort(key=lambda e: e.get("at") or 0)

    merged = dict(ours)
    merged.update({
        "cycle": max(ours.get("cycle", 0), theirs.get("cycle", 0)),
        "sold": sold, "archive": archive,
        "tracked": tracked, "events": events,
    })

    gained = len(sold) - before
    recovered = len(sold) - len(ours.get("sold", {}))
    print(f"merged: sold {before} -> {len(sold)}"
          f"{f'  (+{recovered} recovered from remote)' if recovered else ''}, "
          f"archive {len(archive):,}, tracked {len(tracked):,}", flush=True)

    tmp = STATE + ".tmp"
    json.dump(merged, open(tmp, "w"), separators=(",", ":"))
    os.replace(tmp, STATE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
