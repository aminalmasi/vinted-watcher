"""Vinted brand name -> Vestiaire brand id.

apiv2/brands answers the cluster directly, so this needs no proxy and no CI
round-trip. The list is 17.5k entries and ~1.6 MB, so it is cached on disk and
refetched only when missing.
"""

from __future__ import annotations

import json
import os
import re

import requests

CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "vc_brands.json")
URL = ("https://apiv2.vestiairecollective.com/brands"
       "?isoCountry=IT&x-siteid=12&x-language=it&x-currency=EUR")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def load() -> list:
    if os.path.exists(CACHE):
        try:
            return json.load(open(CACHE))
        except ValueError:
            pass
    r = requests.get(URL, headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    data = [{"id": b["id"], "name": b["name"], "active": b.get("active")}
            for b in r.json().get("data", [])]
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(data, open(CACHE, "w"), ensure_ascii=False)
    return data


def resolve(name: str) -> tuple[str | None, str | None]:
    """Exact normalised match, then a containment fallback.

    Deliberately conservative: a wrong brand silently produces a confident,
    meaningless comparison, so an ambiguous name returns nothing rather than a
    guess. Vinted and Vestiaire disagree on a few (Vinted "Maison Margiela" vs
    Vestiaire "Maison Martin Margiela"), which containment handles.
    """
    n = _norm(name)
    if not n:
        return None, None
    brands = load()
    for b in brands:
        if _norm(b["name"]) == n:
            return str(b["id"]), b["name"]

    # Token overlap, not substring containment. Containment picked
    # "Maison Margiela X Reebok" for "Maison Margiela", because the collab
    # CONTAINS the query while the real brand ("Maison Martin Margiela") does
    # not. Jaccard over word tokens scores the real brand 0.67 and the collab
    # 0.50, which is the ordering we want.
    def toks(x):
        return {t for t in re.split(r"[^a-z0-9]+", (x or "").lower()) if t}
    want = toks(name)
    best = None
    for b in brands:
        if not b.get("active"):
            continue
        t = toks(b["name"])
        if not (t & want):
            continue
        score = len(t & want) / len(t | want)
        key = (score, -len(_norm(b["name"])))
        if best is None or key > best[0]:
            best = (key, b)
    if best and best[0][0] >= 0.5:
        return str(best[1]["id"]), best[1]["name"]
    return None, None


if __name__ == "__main__":
    import sys
    for q in sys.argv[1:]:
        print(f"{q:28} -> {resolve(q)}")
