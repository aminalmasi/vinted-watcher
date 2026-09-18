"""Recover exactly WHICH photos were put in front of the annotator.

The label file records `shown: 14` but not the ids, which is a gap in the
annotation format: without the ids, every non-rejected member of an annotated
group would be treated as verified, including the ones nobody ever saw. Those
must be junk, not positives.

The page sampling was deterministic (seed 17 over unchanged inputs), so the
exact selection is reproducible. The recorded `shown` counts are then used as
a checksum - if the reconstruction disagrees with them, it is wrong and must
not be used.
"""
from __future__ import annotations
import json, os, random, sys

DATA = os.path.expanduser("~/vestiaire_data")
GT = f"{DATA}/ground_truth"
N_GROUPS, PER_GROUP = 120, 14


def main() -> int:
    gs = {g["group_id"]: g for g in
          (json.loads(l) for l in open(f"{GT}/groups.jsonl", encoding="utf-8"))}
    integ = {r["group_id"]: r for r in json.load(open(f"{GT}/group_integrity.json"))}
    elig = []
    for gid, r in integ.items():
        wi = [it for it in gs[gid]["items"] if it.get("images")]
        if len(wi) >= 6:
            elig.append((r["coh"], r["n"], gid, wi))
    random.seed(17)
    cells = {}
    for coh, n, gid, wi in elig:
        ck = "tight" if coh >= 1.15 else ("mid" if coh >= 0.95 else "loose")
        sk = "big" if n >= 200 else ("mid" if n >= 30 else "small")
        cells.setdefault((ck, sk), []).append((gid, wi, coh, n))
    per_cell = max(1, N_GROUPS // max(len(cells), 1))
    chosen = []
    for k, v in sorted(cells.items()):
        chosen += [(k,) + x for x in random.sample(v, min(per_cell, len(v)))]
    random.shuffle(chosen)

    shown = {}
    for (cell, gid, wi, coh, n) in chosen:
        random.shuffle(wi)
        ids = [str(it["id"]) for it in wi[:PER_GROUP] if it.get("images")]
        shown[gid] = ids

    lab = json.load(open(f"{GT}/labels_all.json"))
    ok = bad = 0
    for g in lab:
        rec = shown.get(g["gid"])
        if rec is None:
            bad += 1; continue
        (ok if len(rec) == g["shown"] else bad).__class__  # noop guard
        if len(rec) == g["shown"]:
            ok += 1
        else:
            bad += 1
            print(f"  MISMATCH gid={g['gid']} {g['model']}: "
                  f"recorded {g['shown']}, reconstructed {len(rec)}")
    print(f"checksum: {ok}/{len(lab)} groups match their recorded shown count")
    if bad:
        print("reconstruction is NOT reliable - do not use")
        return 1
    json.dump(shown, open(f"{GT}/shown_ids.json", "w"))
    print(f"wrote {GT}/shown_ids.json ({sum(len(v) for v in shown.values())} photo ids)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
