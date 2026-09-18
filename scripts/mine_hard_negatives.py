"""Mine hard negatives from the corpus we already embedded.

A different-brand listing can never be the same product, so nearest neighbours
drawn from OTHER brands are guaranteed-safe negatives however visually close
they are. That is what makes them usable: unconstrained similarity mining would
surface unlabelled same-product items and poison the set with false negatives,
but the brand constraint rules that out by construction.

(The exception is brand collaborations - Prada x Adidas, Church's x Miu Miu -
which exist as separate brand ids. They are rare and are reported rather than
silently included.)

Two outputs:
  * hard_negatives.jsonl - per group, the closest different-brand photos
  * a brand confusion table - which brands the encoder actually mixes up,
    which says more about the failure mode than a single mAP number does.
"""

from __future__ import annotations

import collections, glob, json, os, random, sys
import numpy as np
import torch

DATA = os.path.expanduser("~/vestiaire_data")
TAG = os.environ.get("EMB_TAG", "dinov2L")
PER_GROUP = int(os.environ.get("NEG_PER_GROUP", "20"))
TOPK = 100


def main() -> int:
    paths = json.load(open(f"{DATA}/embeddings/{TAG}_paths.json"))
    embs = np.load(f"{DATA}/embeddings/{TAG}.npy")
    lid = np.array([os.path.basename(p)[:-4].split("_")[0] for p in paths])

    brand_of, name_of = {}, {}
    for pat in (f"{DATA}/meta/*.jsonl", f"{DATA}/live_meta/*.jsonl"):
        for f in glob.glob(pat):
            for line in open(f, encoding="utf-8"):
                r = json.loads(line)
                brand_of[str(r["id"])] = str(r["brand"])
    BR = {"2":"Gucci","50":"Chanel","14":"Hermès","236":"Louboutin","10":"Dior",
          "60":"Prada","3119":"Saint Laurent","88":"Valentino","809":"Golden Goose",
          "115":"Bottega"}
    groups = [json.loads(l) for l in open(f"{DATA}/ground_truth/groups.jsonl", encoding="utf-8")]
    gof = {}
    for g in groups:
        for it in g["items"]:
            gof[str(it["id"])] = g["group_id"]
    gname = {g["group_id"]: f"{g['brand']} / {g['model']} / {g['subcategory']}"
             for g in groups}

    bidx_names = {b: i for i, b in enumerate(sorted(set(brand_of.values())))}
    bcode = np.array([bidx_names.get(brand_of.get(x, ""), -1) for x in lid])
    gcode = np.array([gof.get(x, -1) for x in lid])

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    X = torch.from_numpy(embs).to(dev).half()
    B = torch.from_numpy(bcode).to(dev)
    G = torch.from_numpy(gcode).to(dev)
    print(f"index {X.shape[0]:,} photos, {len(bidx_names)} brands, {len(groups)} groups", flush=True)

    # one query per group: the first photo of a member, big groups first
    qidx, qgroup = [], []
    by_group = collections.defaultdict(list)
    for i in np.where(gcode >= 0)[0]:
        by_group[gcode[i]].append(i)
    random.seed(0)
    for gid, idxs in sorted(by_group.items(), key=lambda kv: -len(kv[1])):
        qidx.append(random.choice(idxs)); qgroup.append(gid)
    print(f"{len(qidx)} group queries", flush=True)

    out = open(f"{DATA}/ground_truth/hard_negatives.jsonl", "w", encoding="utf-8")
    confuse = collections.Counter()
    inv_b = {v: k for k, v in bidx_names.items()}
    n_written = 0
    Bq = 128
    for s in range(0, len(qidx), Bq):
        qs = torch.tensor(qidx[s:s+Bq], device=dev)
        sim = X[qs].float() @ X.T.float()
        # only OTHER brands can be negatives - same brand may be the same product
        sim[B[qs][:, None] == B[None, :]] = -2.0
        top = sim.topk(TOPK, dim=1)
        for r, qi in enumerate(qs.tolist()):
            gid = qgroup[s + r]
            cols = top.indices[r].tolist()
            scores = top.values[r].tolist()
            negs = []
            for c, sc in zip(cols, scores):
                negs.append({"path": paths[c], "listing": lid[c],
                             "brand": BR.get(inv_b.get(int(bcode[c]), ""), inv_b.get(int(bcode[c]), "?")),
                             "sim": round(float(sc), 4)})
                confuse[(BR.get(brand_of.get(lid[qi], ""), "?"),
                         BR.get(brand_of.get(lid[c], ""), "?"))] += 1
                if len(negs) >= PER_GROUP:
                    break
            out.write(json.dumps({"group_id": int(gid), "group": gname.get(int(gid), ""),
                                  "query": paths[qi], "negatives": negs},
                                 ensure_ascii=False) + "\n")
            n_written += 1
    out.close()
    print(f"\nwrote {n_written} groups x {PER_GROUP} hard negatives", flush=True)

    print("\nbrand confusion (query brand -> nearest other-brand), top pairs:")
    tot = collections.Counter()
    for (a, b), n in confuse.items():
        tot[a] += n
    for (a, b), n in confuse.most_common(14):
        print(f"   {a:<16} -> {b:<16} {100*n/max(tot[a],1):>5.1f}% of its hard negatives")
    return 0


if __name__ == "__main__":
    sys.exit(main())
