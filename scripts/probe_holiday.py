"""Do holiday sellers explain the false 'sold' alerts?

When a seller switches to holiday mode their whole wardrobe leaves the search at
once, which looks exactly like every one of their listings selling. The user
object carries `is_on_holiday`, so this is checkable — and it needs no baseline,
unlike the counter test.

Compares two groups: sellers whose listings just vanished, and sellers whose
listings are still present. If holiday mode is the cause, the first group is
far more likely to be on holiday.
"""

import json
import logging
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vintedwatch.client import BASE, VintedClient  # noqa: E402

SAMPLE = 20


def flags(client, sid):
    r = client._get(f"{BASE}/api/v2/users/{sid}", tries=2,
                    headers={"Accept": "application/json", "Referer": BASE + "/"})
    if r is None or r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
        return None
    try:
        u = r.json().get("user") or {}
    except ValueError:
        return None
    return {
        "holiday": u.get("is_on_holiday"),
        "banned": u.get("is_account_banned"),
        "status": u.get("account_status"),
        "items": u.get("item_count"),
        "given": u.get("given_item_count"),
    }


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(message)s")
    state = json.load(open("data/state.json"))
    client = VintedClient(token_cache=state.get("token"))
    items = state["items"]

    vanished, present = [], []
    for rec in items.values():
        sid = rec.get("seller_id")
        if not sid:
            continue
        (vanished if rec.get("missing_runs", 0) >= 1 else present).append(sid)
    vanished = list(dict.fromkeys(vanished))
    present = list(dict.fromkeys(present))
    random.shuffle(present)
    print(f"sellers with a vanished listing: {len(vanished)}, with listings present: {len(present)}\n")

    for label, pool in (("VANISHED", vanished[:SAMPLE]), ("PRESENT", present[:SAMPLE])):
        hol = ban = ok = none = 0
        for sid in pool:
            # Unpaced calls got the whole second batch rate-limited last time,
            # which silently invalidated the comparison.
            time.sleep(random.uniform(1.0, 2.0))
            f = flags(client, sid)
            if f is None:
                none += 1
                continue
            print(f"    {sid}: holiday={f['holiday']} banned={f['banned']} "
                  f"status={f['status']} items={f['items']}")
            if f["holiday"]:
                hol += 1
            elif f["banned"] or (f["status"] not in (0, None)):
                ban += 1
            else:
                ok += 1
        n = len(pool)
        print(f"{label:9s} n={n:>3}  on holiday: {hol:>3} ({100*hol/max(n,1):.0f}%)  "
              f"banned/limited: {ban}  normal: {ok}  unreadable: {none}")

    print(f"\n[probe] traffic {client.bytes_uncompressed/1024:.0f} KB")


if __name__ == "__main__":
    main()
