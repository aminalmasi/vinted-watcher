"""Is the encoder keyed on colour, when colour is irrelevant to identity?

The user's definition: a Fifi in black patent and a Fifi in beige suede are the
SAME product. If the encoder disagrees - if same-colour pairs inside a group
score much higher than different-colour pairs - then colour sensitivity is a
direct cause of retrieval failure, not an incidental property.

Colour is read from the listing slug, which encodes it explicitly, so no
extra labelling is needed. Comparison is strictly WITHIN a group, so any gap
is attributable to colour rather than to the products differing.
"""
from __future__ import annotations
import collections, json, os, random, re, sys
import numpy as np
import torch

DATA = os.path.expanduser("~/vestiaire_data")
TAG = "dinov2L"
COLOURS = {"nero","bianco","beige","marrone","blu","multicolore","rosa","rosso",
 "grigio","argentato","dorato","verde","cammello","bordeaux","viola","giallo",
 "ecru","arancione","marina","kaki","turchese","fucsia","corallo","antracite"}


def colour_of(link):
    for t in link.split("/")[-1].split("-"):
        if t in COLOURS:
            return t
    return None


def main() -> int:
    paths = json.load(open(f"{DATA}/embeddings/{TAG}_paths.json"))
    embs = np.load(f"{DATA}/embeddings/{TAG}.npy")
    pos = collections.defaultdict(list)
    for i, p in enumerate(paths):
        pos[os.path.basename(p)[:-4].split("_")[0]].append(i)

    gs = [json.loads(l) for l in open(f"{DATA}/ground_truth/groups.jsonl", encoding="utf-8")]
    link_of = {}
    import glob
    for pat in (f"{DATA}/meta/*.jsonl", f"{DATA}/live_meta/*.jsonl"):
        for f in glob.glob(pat):
            for line in open(f, encoding="utf-8"):
                r = json.loads(line)
                link_of[str(r["id"])] = r["link"]

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    X = torch.from_numpy(embs).to(dev).half()
    same_c, diff_c = [], []
    random.seed(0)
    used = 0
    for g in gs:
        members = []
        for it in g["items"]:
            i = pos.get(str(it["id"]))
            c = colour_of(link_of.get(str(it["id"]), ""))
            if i and c:
                members.append((i[0], c))
        if len(members) < 12:
            continue
        random.shuffle(members); members = members[:60]
        idx = torch.tensor([i for i, _ in members], device=dev)
        v = X[idx].float()
        S = (v @ v.T).cpu().numpy()
        cols = [c for _, c in members]
        for a in range(len(cols)):
            for b in range(a + 1, len(cols)):
                (same_c if cols[a] == cols[b] else diff_c).append(S[a, b])
        used += 1

    same_c, diff_c = np.array(same_c), np.array(diff_c)
    print(f"groups used: {used}")
    print(f"  WITHIN-group pairs, SAME colour     : n={len(same_c):>7}  mean sim {same_c.mean():.4f}")
    print(f"  WITHIN-group pairs, DIFFERENT colour: n={len(diff_c):>7}  mean sim {diff_c.mean():.4f}")
    gap = same_c.mean() - diff_c.mean()
    pooled = np.sqrt((same_c.var() + diff_c.var()) / 2)
    print(f"\n  gap {gap:+.4f}   Cohen's d {gap/pooled:+.2f}")
    print("  (d near 0 -> colour-invariant; d > 0.5 -> colour is driving similarity)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
