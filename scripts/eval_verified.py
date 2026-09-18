"""Retrieval on the HUMAN-VERIFIED test set, at two distractor levels.

Only the 1,287 images a person confirmed are used as queries or positives.
Everything else that shares a group is JUNK: ignored in scoring rather than
counted as an error, because retrieving a genuine same-product photo we simply
never checked is not a mistake. That is the Oxford convention and it matters
here - raw slug labels are 94.6% correct, so scoring against them directly
would charge every encoder for ~5% label noise.

Two levels, as requested, and they answer different questions:

  same-brand   distractors restricted to the query's own brand. This is the
               DEPLOYMENT condition: brand always comes from metadata, never
               from pixels, so the encoder is only ever asked to tell products
               apart within one house. Harder per candidate, smaller index.
  all-brands   the full corpus. Bigger index, but cross-brand confusions are
               errors the brand filter would have removed for free.
"""
from __future__ import annotations
import collections, glob, json, os, sys
import numpy as np
import torch

DATA = os.path.expanduser("~/vestiaire_data")
GT = f"{DATA}/ground_truth"
TOPK = 100


def load_labels():
    lab = json.load(open(f"{GT}/labels_all.json"))
    rejected, groups = set(), {}
    for g in lab:
        if g["skipped"]:
            continue
        rejected |= set(map(str, g["bad"]))
        groups[g["gid"]] = g
    return groups, rejected


def main() -> int:
    name = os.environ.get("EMB_NAME", "dinov2L")
    paths = json.load(open(f"{GT}/../embeddings/{name}_paths.json"))
    embs = np.load(f"{GT}/../embeddings/{name}.npy")
    lid = np.array([os.path.basename(p)[:-4].split("_")[0] for p in paths])

    gs = {g["group_id"]: g for g in
          (json.loads(l) for l in open(f"{GT}/groups.jsonl", encoding="utf-8"))}
    ann, rejected = load_labels()

    brand_of = {}
    for pat in (f"{DATA}/meta/*.jsonl", f"{DATA}/live_meta/*.jsonl"):
        for f in glob.glob(pat):
            for line in open(f, encoding="utf-8"):
                r = json.loads(line)
                brand_of[str(r["id"])] = str(r["brand"])

    # Which photos were actually put in front of a person (reconstructed and
    # checksummed against the recorded counts). Everything else in an annotated
    # group was never looked at, so it is JUNK: ignored, not scored either way.
    shown = json.load(open(f"{GT}/shown_ids.json"))
    verified, junk = {}, {}
    for gid_s, ids in shown.items():
        gid = int(gid_s)
        if gid not in ann:
            continue
        seen = set(ids)
        for sid in seen:
            if sid not in rejected:
                verified[sid] = gid
        for it in gs[gid]["items"]:
            sid = str(it["id"])
            if sid not in seen:
                junk[sid] = gid            # same product, never verified

    gcode = np.array([verified.get(x, -1) for x in lid])
    jcode = np.array([junk.get(x, -1) for x in lid])
    bcodes = sorted(set(brand_of.values()))
    bmap = {b: i for i, b in enumerate(bcodes)}
    bcode = np.array([bmap.get(brand_of.get(x, ""), -1) for x in lid])

    # Query photo is now FIXED to the first shot, not sampled at random.
    # Sampling uniformly over photos meant a third of queries were "_3" - back
    # views, soles, detail crops - which retrieve ~19% worse than "_1". That
    # made the headline number an average over a variable nobody chose.
    base = [os.path.basename(p_)[:-4] for p_ in paths]
    photo_i = np.array([int(b.split("_")[1]) if "_" in b else 0 for b in base])
    QP = os.environ.get("QUERY_PHOTO", "first")
    qidx = np.where(gcode >= 0)[0]
    if QP == "first":
        qidx = np.array([i for i in qidx if photo_i[i] <= 1])
        print(f"  queries restricted to first photo: {len(qidx):,}", flush=True)
    print(f"{name}: index {len(paths):,} · verified photos {len(qidx):,} · "
          f"groups {len(ann)}", flush=True)

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    X = torch.from_numpy(embs).to(dev).half()
    G = torch.from_numpy(gcode).to(dev)
    J = torch.from_numpy(jcode).to(dev)
    B = torch.from_numpy(bcode).to(dev)
    L = {v: k for k, v in enumerate(sorted(set(lid)))}
    Li = torch.tensor([L[x] for x in lid], device=dev)

    # The set of valid queries must be fixed BEFORE any model sees the data:
    # a query is valid if at least one OTHER verified listing shares its group.
    # Scoring only the queries that happened to retrieve something lets a weak
    # model quietly drop its failures, which is what made all-brands mAP come
    # out ABOVE same-brand - an impossible ordering, since restricting to one
    # brand only ever removes candidates.
    by_group = collections.defaultdict(set)
    for i in qidx:
        by_group[int(gcode[i])].add(str(lid[i]))
    valid = np.array([i for i in qidx
                      if len(by_group[int(gcode[i])] - {str(lid[i])}) > 0])
    print(f"  valid queries (>=1 other verified listing in group): {len(valid):,}",
          flush=True)

    # Only score against items whose product is KNOWN. 231,031 listings (70%)
    # carry no model token, and a Dior Gang pump titled "Decollete in Pelle"
    # sits among them - scored as a distractor while being the query's product.
    # Measuring that penalises the encoder for being right: 62.5% of "wrong"
    # top-10 results were unlabelled listings, so every earlier number was a
    # deflated lower bound.
    #
    # An unlabelled listing of a DIFFERENT brand is still a safe negative
    # (different brand cannot be the same product), so only unlabelled listings
    # of the QUERY'S OWN brand are removed.
    in_group = np.zeros(len(lid), dtype=bool)
    all_grouped = {str(it["id"]) for g in gs.values() for it in g["items"]}
    for i, x in enumerate(lid):
        in_group[i] = x in all_grouped
    IG = torch.from_numpy(in_group).to(dev)

    by_group = collections.defaultdict(set)
    for i in qidx:
        by_group[int(gcode[i])].add(str(lid[i]))
    valid = np.array([i for i in qidx
                      if len(by_group[int(gcode[i])] - {str(lid[i])}) > 0])
    print(f"  valid queries: {len(valid):,}", flush=True)

    res = {}
    for level in ("same-brand", "all-brands"):
        aps, p1, p10, found, idxsz = [], [], [], 0, []
        for s_ in range(0, len(valid), 128):
            q = torch.tensor(valid[s_:s_+128], device=dev)
            sim = X[q].float() @ X.T.float()
            sim[Li[q][:, None] == Li[None, :]] = -2           # own listing
            same_g = J[None, :] == G[q][:, None]
            sim[same_g & (G[None, :] < 0)] = -2               # junk
            same_brand = B[q][:, None] == B[None, :]
            sim[same_brand & ~IG[None, :]] = -2               # ambiguous
            if level == "same-brand":
                sim[~same_brand] = -2
            idxsz.append(int((sim[0] > -2).sum().item()))
            top = sim.topk(TOPK, dim=1).indices
            for r, qi in enumerate(q.tolist()):
                rel = (G[top[r]] == G[qi]).float()
                n = rel.sum().item()
                p1.append(rel[0].item()); p10.append(rel[:10].mean().item())
                if n:
                    found += 1
                    c = torch.cumsum(rel, 0)
                    ranks = torch.arange(1, TOPK+1, device=dev).float()
                    aps.append((((c/ranks)*rel).sum()/n).item())
                else:
                    aps.append(0.0)
        res[level] = (float(np.mean(p1)), float(np.mean(p10)),
                      float(np.mean(aps)), len(aps), found)
        print(f"  {level:<11} P@1 {np.mean(p1):.3f}  P@10 {np.mean(p10):.3f}  "
              f"mAP@{TOPK} {np.mean(aps):.3f}  ({len(aps):,} queries, "
              f"{found:,} found, index ~{int(np.mean(idxsz)):,})", flush=True)
    json.dump({k: list(v) for k, v in res.items()},
              open(f"{GT}/eval_{name}.json", "w"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
