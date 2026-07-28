"""Find why live listings are being reported as sold.

Suspects, in order of likelihood:
  1. `total_entries: 960` is a CAP, not a true count. If so the sweep is still a
     window — just a bigger one — and old listings age out while staying live.
     The false positives all have low ids (old listings), which fits.
  2. Those listings were dropped before `seller_id` existed, so the wardrobe gate
     could not verify them and the code alerted anyway.
"""

import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vintedwatch.client import BASE, VintedClient  # noqa: E402

SUSPECTS = {
    8816402278: "Prada black sneakers 40",
    9006117182: "Prada heels sandals",
    9100346010: "prada heels",
    9258549163: "Escarpins Prada nude ajourés cuir Made in Italy",
}


def totals(client, text, per_page=96):
    r = client._get(BASE + "/api/v2/catalog/items",
                    params={"search_text": text, "order": "newest_first",
                            "per_page": per_page, "page": 1},
                    tries=2, headers={"Accept": "application/json",
                                      "Referer": BASE + "/catalog"})
    if r is None or r.status_code != 200:
        return None
    return (r.json().get("pagination") or {})


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(message)s")
    state = json.load(open("data/state.json"))
    client = VintedClient(token_cache=state.get("token"))

    print("=== 1. is total_entries a real count or a cap? ===")
    for text in ("prada shoes", "prada", "nike", "shoes"):
        pag = totals(client, text)
        if pag:
            print(f"  {text:14s} -> total_entries={pag.get('total_entries')} "
                  f"total_pages={pag.get('total_pages')} per_page={pag.get('per_page')}")

    print("\n=== 2. are the false positives actually still live? ===")
    for item_id, title in SUSPECTS.items():
        r = client._get(BASE + "/api/v2/catalog/items",
                        params={"search_text": title, "order": "newest_first",
                                "per_page": 96, "page": 1},
                        tries=2, headers={"Accept": "application/json",
                                          "Referer": BASE + "/catalog"})
        if r is None or r.status_code != 200:
            print(f"  {item_id}: search failed")
            continue
        items = r.json().get("items", [])
        hit = next((i for i in items if i.get("id") == item_id), None)
        if hit:
            sid = (hit.get("user") or {}).get("id")
            print(f"  {item_id}: STILL LIVE — found by title search, seller={sid}")
            print(f"      wardrobe says still_listed={client.still_listed(item_id, sid)}")
        else:
            print(f"  {item_id}: not found in {len(items)} title-search results "
                  f"(probably really gone)")

    print(f"\n[probe] traffic {client.bytes_uncompressed / 1024:.0f} KB")


if __name__ == "__main__":
    main()
