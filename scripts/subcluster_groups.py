"""Propose visual sub-variants inside the low-coherence groups, for human review.

Groups like Louboutin Fifi (506 listings, coherence 0.82) are real products whose
name does not determine appearance: one model across many colours, materials and
heel heights. Whether that should be ONE group or several is a question about the
benchmark, not about the data, so this proposes splits and leaves the judgement
to a person.

Circularity warning, deliberately not hidden: the clusters come from DINOv2, the
same encoder we evaluate. Accepting them unreviewed would produce a ground truth
that DINOv2 defines and therefore aces. Human accept/reject/merge is what makes
the resulting labels independent - the machine proposes candidates, the person
decides. Only reviewed splits should enter an evaluation set.

Clustering is agglomerative with a cosine threshold rather than k-means: the
number of real variants is unknown and differs per group, so fixing k would
impose an answer instead of finding one.
"""
from __future__ import annotations
import base64, collections, io, json, os, sys
import numpy as np
from PIL import Image
from sklearn.cluster import AgglomerativeClustering

DATA = os.path.expanduser("~/vestiaire_data")
OUT = f"{DATA}/ground_truth/subclusters.html"
TAG = "dinov2L"
THRESH = float(os.environ.get("SUB_THRESH", "0.45"))   # cosine distance
MAX_SHOW = 10


def b64(rel, size=110):
    try:
        with Image.open(os.path.join(DATA, rel)) as im:
            im = im.convert("RGB"); im.thumbnail((size, size))
            b = io.BytesIO(); im.save(b, "JPEG", quality=70)
        return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()
    except Exception:
        return None


def main() -> int:
    paths = json.load(open(f"{DATA}/embeddings/{TAG}_paths.json"))
    embs = np.load(f"{DATA}/embeddings/{TAG}.npy")
    pos = collections.defaultdict(list)
    for i, p in enumerate(paths):
        pos[os.path.basename(p)[:-4].split("_")[0]].append(i)

    integ = {r["group_id"]: r for r in
             json.load(open(f"{DATA}/ground_truth/group_integrity.json"))}
    gs = {g["group_id"]: g for g in
          (json.loads(l) for l in open(f"{DATA}/ground_truth/groups.jsonl", encoding="utf-8"))}

    # the biggest low-coherence groups: where a split would matter most
    targets = sorted((r for r in integ.values() if r["coh"] < 0.95 and r["n"] >= 60),
                     key=lambda r: -r["n"])[:6]
    print("groups to split:", [(t["model"], t["n"], round(t["coh"], 2)) for t in targets])

    sections = []
    for t in targets:
        g = gs[t["group_id"]]
        items = [(it, pos[str(it["id"])][0]) for it in g["items"] if pos.get(str(it["id"]))]
        if len(items) < 20:
            continue
        items = items[:300]
        V = embs[[i for _, i in items]].astype(np.float32)
        V /= np.linalg.norm(V, axis=1, keepdims=True) + 1e-9
        lab = AgglomerativeClustering(n_clusters=None, distance_threshold=THRESH,
                                      metric="cosine", linkage="average").fit_predict(V)
        by = collections.defaultdict(list)
        for (it, _), c in zip(items, lab):
            by[c].append(it)
        big = sorted(by.values(), key=len, reverse=True)
        keep = [c for c in big if len(c) >= 3][:8]
        singles = sum(len(c) for c in big if len(c) < 3)

        blocks = []
        for ci, members in enumerate(keep, 1):
            imgs = []
            for it in members[:MAX_SHOW]:
                b = b64(it["images"][0]) if it.get("images") else None
                if b:
                    imgs.append(f'<img src="{b}" title="{(it.get("name") or "")[:40]}">')
            blocks.append(f'<div class=cl><div class=h>variant {ci} '
                          f'<span>({len(members)} listings)</span></div>'
                          f'<div class=imgs>{"".join(imgs)}</div></div>')
        sections.append(
            f'<section><h2>{g["brand"]} &middot; {g["model"]} &middot; {g["subcategory"]}'
            f'<span class=meta> {g["n"]} listings &middot; coherence '
            f'{t["coh"]:.2f} &middot; {len(keep)} variants shown, {singles} left over</span>'
            f'</h2>{"".join(blocks)}</section>')

    html = f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Proposed variants</title>
<style>:root{{color-scheme:light dark;--bg:#f6f5f3;--card:#fff;--tx:#171717;--dim:#6f6f6f;--line:#e4e1dc}}
@media(prefers-color-scheme:dark){{:root{{--bg:#121212;--card:#1d1d1d;--tx:#ececec;--dim:#9a9a9a;--line:#2c2c2c}}}}
body{{margin:0;background:var(--bg);color:var(--tx);font:13px/1.5 ui-sans-serif,-apple-system,Arial}}
header{{padding:16px 20px;border-bottom:1px solid var(--line)}} h1{{margin:0;font-size:18px}}
.sub{{color:var(--dim);font-size:13px;margin-top:6px;max-width:80ch}}
section{{padding:14px 20px;border-bottom:1px solid var(--line)}}
h2{{margin:0 0 10px;font-size:14px}} .meta{{color:var(--dim);font-weight:400;font-size:12px}}
.cl{{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:8px;margin-bottom:8px}}
.h{{font-size:12px;font-weight:650;margin-bottom:6px}} .h span{{color:var(--dim);font-weight:400}}
.imgs{{display:flex;gap:6px;flex-wrap:wrap}}
.imgs img{{width:84px;height:84px;object-fit:cover;border-radius:6px;border:1px solid var(--line)}}
</style></head><body><header><h1>Proposed variants inside generic-name groups</h1>
<div class=sub>Each block is one model whose name does not pin down appearance. Within it,
proposed <b>variants</b> found by clustering the photos. Question for you, per block:
is splitting this into variants the right target, or is the whole model one group?
Clusters are machine-proposed from DINOv2 &mdash; they only become usable ground truth
once a person accepts, rejects or merges them.</div></header>
{''.join(sections)}</body></html>"""
    open(OUT, "w", encoding="utf-8").write(html)
    print(f"wrote {OUT} ({os.path.getsize(OUT)/1024/1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
