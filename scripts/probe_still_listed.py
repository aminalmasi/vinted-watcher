"""Validate still_listed() before trusting it to gate every alert.

It must answer False for a listing we know sold and True for one we know is
live. If it wrongly said True the watcher would go silent — the exact failure
mode we cannot detect from the outside.
"""

import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vintedwatch.client import VintedClient  # noqa: E402

# Confirmed sold on 2026-07-27 by reading item_closing_action="sold".
KNOWN_SOLD = {9505849905: 32001697, 9493035670: 3138688371}


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(message)s")
    state = json.load(open("data/state.json"))
    client = VintedClient(token_cache=state.get("token"))

    print("=== A. listings we KNOW sold — must be False ===")
    ok = True
    for item_id, seller_id in KNOWN_SOLD.items():
        got = client.still_listed(item_id, seller_id)
        verdict = "PASS" if got is False else "FAIL"
        ok &= got is False
        print(f"  [{verdict}] item {item_id}: still_listed={got} (expected False)")

    print("\n=== B. listings we KNOW are live — must be True ===")
    feed = client.search({"search_text": "prada shoes", "order": "newest_first",
                          "per_page": 24}, page=1) or []
    tested = 0
    for it in feed:
        sid = (it.get("user") or {}).get("id")
        if not sid:
            continue
        got = client.still_listed(it["id"], sid)
        verdict = "PASS" if got is True else "FAIL"
        ok &= got is True
        print(f"  [{verdict}] item {it['id']}: still_listed={got} (expected True) "
              f"| {str(it.get('title'))[:34]}")
        tested += 1
        if tested >= 3:
            break

    print("\n=== C. missing seller id must be None, not a guess ===")
    got = client.still_listed(123456789, None)
    print(f"  [{'PASS' if got is None else 'FAIL'}] still_listed=None (expected None)")
    ok &= got is None

    print(f"\nRESULT: {'all checks passed' if ok else 'FAILURES ABOVE — do not trust the gate'}")
    print(f"[probe] traffic {client.bytes_uncompressed / 1024:.0f} KB uncompressed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
