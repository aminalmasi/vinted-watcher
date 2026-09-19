"""Per-brand price bands for Vinted discovery.

Vinted stops paginating a search at ~960 listings, so one query per brand can
never see more than that however many pages are requested. Splitting a brand's
search into price bands gives each band its own pagination depth, and a probe
confirmed the price filters are honoured exactly (100% of returned items fell
inside the requested band, for every band of both brands tested).

The bands are PER BRAND, because one global set of edges fits nobody: the same
six global edges put 44% of Prada into a single band and 56% of Hermes into a
different single band, since their medians differ five-fold (EUR 80 vs 400).
Per-brand quantiles give every band a roughly equal share by construction.

Caveat worth knowing: the quantiles are computed from the ALREADY-CAPPED feed,
so the expensive tail is under-represented and the top band will saturate
first. The top band is therefore left open-ended and is the one to split again
if it comes back full.
"""
from __future__ import annotations
import json, os, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(REPO, "data", "vinted_track.json")
OUT = os.path.join(REPO, "data", "vinted_bands.json")
NBANDS = int(os.environ.get("VT_NBANDS", "6"))
OPEN_TOP = 100000


def compute(prices: list[float], k: int = NBANDS) -> list[int]:
    """Equal-count edges, deduplicated.

    Rounding two adjacent quantiles to the same euro would create an empty
    band that still costs a full page request, so collapsed edges are dropped
    rather than kept.
    """
    ps = sorted(prices)
    n = len(ps)
    edges = [0]
    for i in range(1, k):
        e = int(round(ps[i * n // k]))
        if e > edges[-1]:
            edges.append(e)
    edges.append(OPEN_TOP)
    return edges


def main() -> int:
    st = json.load(open(STATE))
    by: dict[str, list[float]] = {}
    for v in (st.get("tracked") or {}).values():
        try:
            p = float(v.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if p > 0:
            by.setdefault(v.get("brand") or "?", []).append(p)

    bands = {}
    for b, ps in sorted(by.items()):
        if len(ps) < 50:
            print(f"  {b:<22} only {len(ps)} priced - no bands", flush=True)
            continue
        bands[b] = compute(ps)
        share = [sum(1 for p in ps if bands[b][i] <= p < bands[b][i + 1])
                 for i in range(len(bands[b]) - 1)]
        print(f"  {b:<22} n={len(ps):>4} {str(bands[b]):<42} "
              + " ".join(f"{100*x/len(ps):>3.0f}%" for x in share), flush=True)

    json.dump(bands, open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT} ({len(bands)} brands)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
