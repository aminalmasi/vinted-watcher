"""Deployment setup: one photo per listing in the index, several photos as the query.

This mirrors the real task rather than the convenient one. The database holds a
single canonical photo per listing (sold listings only ever have one, and a live
listing's "_1" is the equivalent main shot - their view mixes are near identical,
62%/58.7% pair/angled vs 57%/58.8%). The person searching, by contrast, has the
shoe in hand and can photograph it from several angles.

Two aggregations, because they fail differently:
  mean  average the L2-normalised photo embeddings, then renormalise. Smooth,
        but a bad view (a box, a logo crop) drags the centroid off the product.
  max   score each query photo against the index and keep the best hit per
        candidate. Robust to one useless view, since a single good photo can
        still find the match on its own.

Queries are restricted to listings with at least 3 photos so that 1 vs 2 vs 3
is measured on the SAME listings - otherwise the 3-photo condition would quietly
be evaluated on a different, live-only subset and the comparison would be
between populations rather than between methods.
"""
from __future__ import annotations
import collections, glob, json, os, sys
import numpy as np
import torch

DATA = os.path.expanduser("~/vestiaire_data")
GT = f"{DATA}/ground_truth"
NAME = os.environ.get("EMB_NAME", "siglip2")
TOPK = 100


def main() -> int:
    paths = json.load(open(f"{DATA}/embeddings/{NAME}_paths.json"))
    embs = np.load(f"{DATA}/embeddings/{NAME}.npy")
    base = [os.path.basename(p)[:-4] for p in paths]
    lid = np.array([b.split("_")[0] for b in base])
    pidx = np.array([int(b.split("_")[1]) if "_" in b else 0 for b in base])

    gs = {g["group_id"]: g for g in
          (json.loads(l) for l in open(f"{GT}/groups.jsonl", encoding="utf-8"))}
    lab = json.load(open(f"{GT}/labels_all.json"))
    ann = {g["gid"]: g for g in lab if not g["skipped"]}
    rejected = {str(x) for g in lab if not g["skipped"] for x in g["bad"]}
    shown = json.load(open(f"{GT}/shown_ids.json"))
    verified, junk = {}, {}
    for k, ids in shown.items():
        gid = int(k)
        if gid not in ann:
            continue
        seen = set(ids)
        for s in seen:
            if s not in rejected:
                verified[s] = gid
        for it in gs[gid]["items"]:
            s = str(it["id"])
            if s not in seen:
                junk[s] = gid

    brand_of = {}
    for pat in (f"{DATA}/meta/*.jsonl", f"{DATA}/live_meta/*.jsonl"):
        for f in glob.glob(pat):
            for line in open(f, encoding="utf-8"):
                r = json.loads(line)
                brand_of[str(r["id"])] = str(r["brand"])
    all_grouped = {str(it["id"]) for g in gs.values() for it in g["items"]}

    # INDEX: exactly one photo per listing - the first
    first = pidx <= 1
    idx_rows = np.where(first)[0]
    print(f"{NAME}: index = {len(idx_rows):,} photos (1 per listing), "
          f"from {len(paths):,} total", flush=True)

    gcode = np.array([verified.get(x, -1) for x in lid])
    jcode = np.array([junk.get(x, -1) for x in lid])
    ingrp = np.array([x in all_grouped for x in lid])
    bmap = {b: i for i, b in enumerate(sorted(set(brand_of.values())))}
    bcode = np.array([bmap.get(brand_of.get(x, ""), -1) for x in lid])

    dev = "cuda"
    Xi = torch.from_numpy(embs[idx_rows]).to(dev).half()      # index matrix
    Gi = torch.from_numpy(gcode[idx_rows]).to(dev)
    Ji = torch.from_numpy(jcode[idx_rows]).to(dev)
    IGi = torch.from_numpy(ingrp[idx_rows]).to(dev)
    Bi = torch.from_numpy(bcode[idx_rows]).to(dev)
    Lid_i = np.array(lid[idx_rows])

    # queries: verified listings with >=3 photos, and >=1 other verified listing
    photos_of = collections.defaultdict(list)
    for i, x in enumerate(lid):
        photos_of[x].append(i)
    by_group = collections.defaultdict(set)
    for x, g in verified.items():
        by_group[g].add(x)
    qlist = [x for x, g in verified.items()
             if len(photos_of.get(x, [])) >= 3 and len(by_group[g] - {x}) > 0]
    print(f"  query listings with >=3 photos: {len(qlist):,}", flush=True)

    def run(nphotos, mode, level):
        aps, p1, p10 = [], [], []
        for s in range(0, len(qlist), 64):
            chunk = qlist[s:s+64]
            sims = []
            for x in chunk:
                rows = sorted(photos_of[x], key=lambda i: pidx[i])[:nphotos]
                V = torch.from_numpy(embs[rows]).to(dev).float()
                if mode == "mean":
                    v = torch.nn.functional.normalize(V.mean(0, keepdim=True), dim=-1)
                    sims.append((v @ Xi.T.float())[0])
                else:
                    sims.append((V @ Xi.T.float()).max(0).values)
            S = torch.stack(sims)
            qb = torch.tensor([bmap.get(brand_of.get(x, ""), -1) for x in chunk], device=dev)
            qg = torch.tensor([verified[x] for x in chunk], device=dev)
            own = torch.tensor([[Lid_i[j] == x for j in range(len(Lid_i))]
                                for x in chunk], device=dev) if False else None
            for r, x in enumerate(chunk):
                S[r][torch.from_numpy(Lid_i == x).to(dev)] = -2          # own listing
            S[(Ji[None, :] == qg[:, None]) & (Gi[None, :] < 0)] = -2      # junk
            same_b = qb[:, None] == Bi[None, :]
            S[same_b & ~IGi[None, :]] = -2                               # ambiguous
            if level == "same-brand":
                S[~same_b] = -2
            top = S.topk(TOPK, dim=1).indices
            for r in range(len(chunk)):
                rel = (Gi[top[r]] == qg[r]).float()
                n = rel.sum().item()
                p1.append(rel[0].item()); p10.append(rel[:10].mean().item())
                if n:
                    c = torch.cumsum(rel, 0)
                    ranks = torch.arange(1, TOPK+1, device=dev).float()
                    aps.append((((c/ranks)*rel).sum()/n).item())
                else:
                    aps.append(0.0)
        return np.mean(p1), np.mean(p10), np.mean(aps)

    print(f"\n{'setup':<22}{'same-brand P@1/P@10/mAP':<30}{'all-brands P@1/P@10/mAP'}")
    for nph, mode, label in ((1, "mean", "1 photo"),
                             (2, "mean", "2 photos, mean"),
                             (3, "mean", "3 photos, mean"),
                             (2, "max", "2 photos, max-sim"),
                             (3, "max", "3 photos, max-sim")):
        sb = run(nph, mode, "same-brand")
        ab = run(nph, mode, "all-brands")
        print(f"{label:<22}{sb[0]:.3f}/{sb[1]:.3f}/{sb[2]:.3f}{'':<12}"
              f"{ab[0]:.3f}/{ab[1]:.3f}/{ab[2]:.3f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
