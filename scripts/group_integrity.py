"""Are the 573 group names real products, or parsing junk?

A name-based check would be circular - the names came from the same slugs. So
this uses an independent signal: VISUAL COHERENCE.

If "oran" names a real product, photos inside that group should look more alike
than a random pair of Hermes sandals does. If a token is junk (a style word, a
size, a typo), its group is just an arbitrary slice of the brand and its
internal similarity will match the brand/subcategory baseline.

  coherence = mean intra-group similarity / mean similarity of a random
              same-brand same-subcategory pair

Above ~1.15 the group is visually tighter than chance and the name is doing
real work. Near 1.0 it is not, whatever the token looks like.

Capitalisation is reported alongside as cheap corroboration: Vestiaire writes
real model names capitalised inside the listing title ("Sandali Oran in Pelle"),
so a token that never appears capitalised is suspect.
"""
from __future__ import annotations
import collections, json, os, random, sys
import numpy as np
import torch

DATA = os.path.expanduser("~/vestiaire_data")
TAG = "dinov2L"
SAMPLE = 40


def main() -> int:
    paths = json.load(open(f"{DATA}/embeddings/{TAG}_paths.json"))
    embs = np.load(f"{DATA}/embeddings/{TAG}.npy")
    lid = np.array([os.path.basename(p)[:-4].split("_")[0] for p in paths])
    pos = collections.defaultdict(list)
    for i, x in enumerate(lid):
        pos[x].append(i)

    gs = [json.loads(l) for l in open(f"{DATA}/ground_truth/groups.jsonl", encoding="utf-8")]
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    X = torch.from_numpy(embs).to(dev).half()

    # baseline pool: every listing of a given (brand, subcategory)
    pool = collections.defaultdict(list)
    for g in gs:
        for it in g["items"]:
            for i in pos.get(str(it["id"]), []):
                pool[(g["brand_id"], g["subcategory"])].append(i)

    def mean_sim(idx):
        if len(idx) < 2:
            return None
        v = X[torch.tensor(idx, device=dev)].float()
        s = v @ v.T
        n = len(idx)
        return float((s.sum() - s.diag().sum()) / (n * (n - 1)))

    random.seed(0)
    out = []
    for g in gs:
        idx = [i for it in g["items"] for i in pos.get(str(it["id"]), [])]
        if len(idx) < 6:
            continue
        idx = random.sample(idx, min(SAMPLE, len(idx)))
        intra = mean_sim(idx)
        base_pool = pool[(g["brand_id"], g["subcategory"])]
        if intra is None or len(base_pool) < 12:
            continue
        base = mean_sim(random.sample(base_pool, min(SAMPLE, len(base_pool))))
        if not base:
            continue
        caps = sum(1 for it in g["items"][:60]
                   if any(w.capitalize() in (it.get("name") or "")
                          for w in g["model"].split("-") if len(w) > 3))
        shown = min(60, len(g["items"]))
        out.append({"group_id": g["group_id"], "brand": g["brand"],
                    "model": g["model"], "sub": g["subcategory"], "n": g["n"],
                    "intra": intra, "base": base, "coh": intra / base,
                    "cap_rate": caps / max(shown, 1)})

    out.sort(key=lambda r: r["coh"])
    json.dump(out, open(f"{DATA}/ground_truth/group_integrity.json", "w"), indent=1)
    cohs = np.array([r["coh"] for r in out])
    print(f"groups scored: {len(out)}")
    print(f"coherence: median {np.median(cohs):.3f}, p10 {np.percentile(cohs,10):.3f}, "
          f"p90 {np.percentile(cohs,90):.3f}")
    print(f"  >=1.15 (clearly tighter than chance): {(cohs>=1.15).sum()}")
    print(f"  1.05-1.15 (weak)                   : {((cohs>=1.05)&(cohs<1.15)).sum()}")
    print(f"  <1.05  (no better than chance)     : {(cohs<1.05).sum()}")

    print(f"\n--- WEAKEST 15 (suspect names) ---")
    print(f"{'coh':>6}{'cap':>6}{'n':>7}  brand / subcat / model")
    for r in out[:15]:
        print(f"{r['coh']:>6.2f}{r['cap_rate']:>6.0%}{r['n']:>7}  "
              f"{r['brand'][:16]:<18}{r['sub'][:16]:<18}{r['model'][:28]}")
    print(f"\n--- STRONGEST 10 ---")
    for r in out[-10:][::-1]:
        print(f"{r['coh']:>6.2f}{r['cap_rate']:>6.0%}{r['n']:>7}  "
              f"{r['brand'][:16]:<18}{r['sub'][:16]:<18}{r['model'][:28]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
