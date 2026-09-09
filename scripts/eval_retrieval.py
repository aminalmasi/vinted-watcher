"""First honest measurement: can an off-the-shelf encoder find the same product?

Protocol, and the reasoning behind each choice:

  * A query is ONE photo. Positives are photos of OTHER listings in the same
    (brand, model, subcategory) group. Photos of the query's own listing are
    EXCLUDED rather than counted correct — live listings carry 3 shots from one
    seller's session, and counting those would measure "can it match the same
    photo session", which is trivial and not the task.

  * The index is the ENTIRE corpus, including the ~230k listings with no model
    token. Those are the distractors, and they are hard ones: same brands, same
    categories, often visually near-identical.

  * Price MAPE is reported alongside mAP because it needs no labels at all and
    measures what the system is actually for. An encoder that confuses two
    colourways of one shoe scores badly on mAP and perfectly on price; if the
    goal is valuation that is a model doing its job.

Ground-truth groups are automatic (slug-derived) and not yet human-verified, so
every number here is an estimate whose ceiling is that labelling accuracy.
"""

from __future__ import annotations

import json, os, random, sys, collections
import numpy as np
import torch

DATA = os.path.expanduser("~/vestiaire_data")
TAG = os.environ.get("EMB_TAG", "dinov2L")
N_QUERIES = int(os.environ.get("N_QUERIES", "3000"))
TOPK = 50


def main() -> int:
    paths = json.load(open(f"{DATA}/embeddings/{TAG}_paths.json"))
    embs = np.load(f"{DATA}/embeddings/{TAG}.npy")
    print(f"index: {embs.shape[0]:,} photos x {embs.shape[1]}", flush=True)

    # photo -> listing id
    lid = []
    for p in paths:
        base = os.path.basename(p)[:-4]
        lid.append(base.split("_")[0])
    lid = np.array(lid)

    groups = [json.loads(l) for l in open(f"{DATA}/ground_truth/groups.jsonl", encoding="utf-8")]
    gof = {}
    for g in groups:
        for it in g["items"]:
            gof[str(it["id"])] = g["group_id"]
    price = {}
    for g in groups:
        for it in g["items"]:
            if it.get("price"):
                price[str(it["id"])] = it["price"] / 100

    # brand for EVERY listing, not just grouped ones - the whole index needs it
    brand_of = {}
    for pat in (f"{DATA}/meta/*.jsonl", f"{DATA}/live_meta/*.jsonl"):
        import glob as _g
        for f in _g.glob(pat):
            for line in open(f, encoding="utf-8"):
                r = json.loads(line)
                brand_of[str(r["id"])] = str(r["brand"])
    bid_names = {b: i for i, b in enumerate(sorted(set(brand_of.values())))}
    bidx = np.array([bid_names.get(brand_of.get(x, ""), -1) for x in lid])
    print(f"brands mapped for {int((bidx>=0).sum()):,} photos", flush=True)

    gid = np.array([gof.get(x, -1) for x in lid])
    labelled = np.where(gid >= 0)[0]
    print(f"photos with a group label: {len(labelled):,}", flush=True)

    # queries: only from groups with >=2 distinct listings that have photos
    per_group = collections.defaultdict(set)
    for i in labelled:
        per_group[gid[i]].add(lid[i])
    ok_groups = {g for g, s in per_group.items() if len(s) >= 2}
    pool = [i for i in labelled if gid[i] in ok_groups]
    random.seed(0)
    qs = random.sample(pool, min(N_QUERIES, len(pool)))
    print(f"queries: {len(qs):,} from {len(ok_groups):,} usable groups", flush=True)

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    X = torch.from_numpy(embs).to(dev).half()
    G = torch.from_numpy(gid).to(dev)
    Bd = torch.from_numpy(bidx).to(dev)
    BRAND_FILTER = os.environ.get("BRAND_FILTER", "0") == "1"
    L = {v: k for k, v in enumerate(sorted(set(lid)))}
    Lidx = torch.tensor([L[x] for x in lid], device=dev)

    aps, p1, p10, ape = [], [], [], []
    B = 256
    for s in range(0, len(qs), B):
        idx = torch.tensor(qs[s:s+B], device=dev)
        sim = (X[idx].float() @ X.T.float())
        # never retrieve the query's own listing (same photo session)
        same_listing = Lidx[idx][:, None] == Lidx[None, :]
        sim[same_listing] = -2.0
        if BRAND_FILTER:
            # Brand from metadata, never from pixels. A black Gucci pump and a
            # black Prada pump are visually near-identical, so appearance
            # cannot decide brand - it can only decide which product WITHIN a
            # brand. Candidates of another brand are removed before ranking.
            sim[Bd[idx][:, None] != Bd[None, :]] = -2.0
        top = sim.topk(TOPK, dim=1).indices
        for r, qi in enumerate(idx.tolist()):
            rel = (G[top[r]] == G[qi]).float()
            n_rel = rel.sum().item()
            p1.append(rel[0].item())
            p10.append(rel[:10].mean().item())
            if n_rel:
                csum = torch.cumsum(rel, 0)
                ranks = torch.arange(1, TOPK + 1, device=dev).float()
                aps.append(((csum / ranks) * rel).sum().item() / n_rel)
            # price estimate from the 10 nearest neighbours
            true = price.get(lid[qi])
            if true:
                ps = [price[lid[j]] for j in top[r][:10].tolist() if lid[j] in price]
                if ps:
                    ape.append(abs(float(np.median(ps)) - true) / true)

    print(f"\n=== {TAG}, {len(qs):,} queries, index {embs.shape[0]:,}, "
          f"brand_filter={BRAND_FILTER} ===")
    print(f"  P@1           {np.mean(p1):.3f}")
    print(f"  P@10          {np.mean(p10):.3f}")
    print(f"  mAP@{TOPK}       {np.mean(aps):.3f}   (over {len(aps):,} queries with a positive)")
    print(f"  price MAPE    {np.median(ape)*100:.1f}%  median, "
          f"{np.mean(ape)*100:.1f}% mean  ({len(ape):,} queries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
