"""Show what SigLIP2 actually retrieves, with the verdict on every result.

A mAP of 0.125 says how often it fails, not HOW. This renders the top 10 for a
sample of queries and marks each result:

  correct     a verified listing from the query's own group
  junk        same group but never human-verified, so it is ignored in scoring
              rather than counted wrong - showing it makes clear how much of an
              apparent failure is really an unscored hit
  wrong       anything else

Queries are drawn across the coherence strata on purpose: tight groups (the
model name pins down appearance) and loose ones (it does not) fail differently,
and a sample of only one kind would misrepresent the model.
"""
from __future__ import annotations
import base64, collections, glob, io, json, os, random, sys
import numpy as np
import torch
from PIL import Image

DATA = os.path.expanduser("~/vestiaire_data")
GT = f"{DATA}/ground_truth"
OUT = f"{GT}/retrieval_siglip2.html"
NAME = os.environ.get("EMB_NAME", "siglip2")
N_Q = int(os.environ.get("N_QUERIES", "24"))
TOPK = 10


def b64(rel, size=120):
    try:
        with Image.open(os.path.join(DATA, rel)) as im:
            im = im.convert("RGB"); im.thumbnail((size, size))
            b = io.BytesIO(); im.save(b, "JPEG", quality=72)
        return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()
    except Exception:
        return None


def main() -> int:
    paths = json.load(open(f"{DATA}/embeddings/{NAME}_paths.json"))
    embs = np.load(f"{DATA}/embeddings/{NAME}.npy")
    lid = np.array([os.path.basename(p)[:-4].split("_")[0] for p in paths])

    gs = {g["group_id"]: g for g in
          (json.loads(l) for l in open(f"{GT}/groups.jsonl", encoding="utf-8"))}
    lab = json.load(open(f"{GT}/labels_all.json"))
    ann = {g["gid"]: g for g in lab if not g["skipped"]}
    rejected = {str(x) for g in lab if not g["skipped"] for x in g["bad"]}
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
                junk[sid] = gid

    brand_of = {}
    for pat in (f"{DATA}/meta/*.jsonl", f"{DATA}/live_meta/*.jsonl"):
        for f in glob.glob(pat):
            for line in open(f, encoding="utf-8"):
                r = json.loads(line)
                brand_of[str(r["id"])] = str(r["brand"])

    gcode = np.array([verified.get(x, -1) for x in lid])
    jcode = np.array([junk.get(x, -1) for x in lid])
    all_grouped = {str(it["id"]) for g in gs.values() for it in g["items"]}
    in_group = np.array([x in all_grouped for x in lid])
    bmap = {b_: i for i, b_ in enumerate(sorted(set(brand_of.values())))}
    bcode = np.array([bmap.get(brand_of.get(x, ""), -1) for x in lid])
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    X = torch.from_numpy(embs).to(dev).half()
    G = torch.from_numpy(gcode).to(dev)
    J = torch.from_numpy(jcode).to(dev)
    L = {v: k for k, v in enumerate(sorted(set(lid)))}
    Li = torch.tensor([L[x] for x in lid], device=dev)
    IG = torch.from_numpy(in_group).to(dev)
    B = torch.from_numpy(bcode).to(dev)

    by_group = collections.defaultdict(set)
    qidx = np.where(gcode >= 0)[0]
    for i in qidx:
        by_group[int(gcode[i])].add(str(lid[i]))
    valid = [i for i in qidx if len(by_group[int(gcode[i])] - {str(lid[i])}) > 0]

    # spread across coherence strata so the sample is not all easy groups
    strat = collections.defaultdict(list)
    for i in valid:
        strat[ann[int(gcode[i])]["cell"].split("/")[0]].append(i)
    random.seed(5)
    chosen = []
    for k in ("tight", "mid", "loose"):
        chosen += random.sample(strat[k], min(N_Q // 3, len(strat[k])))
    random.shuffle(chosen)

    rows, tally = [], collections.Counter()
    for qi in chosen:
        g = gs[int(gcode[qi])]
        sim = (X[torch.tensor([qi], device=dev)].float() @ X.T.float())[0]
        sim[Li == Li[qi]] = -2                    # own listing
        sim[(J == G[qi]) & (G < 0)] = -2          # junk: same group, unverified
        # Same pool as the corrected evaluation: unlabelled listings of the
        # query own brand are dropped, since any of them could silently BE the
        # query product. Without this, 62.5 percent of "wrong" results were
        # unlabelled same-brand listings that were often plainly correct.
        sim[(B == B[qi]) & ~IG] = -2
        top = sim.topk(TOPK).indices.tolist()
        cards = []
        for r in top:
            if gcode[r] == gcode[qi]:
                cls, lbl = "ok", "correct"
            elif jcode[r] == gcode[qi]:
                cls, lbl = "jk", "junk (same group, unverified)"
            else:
                cls, lbl = "no", gs.get(int(gcode[r]), {}).get("model", "") or "wrong"
            tally[cls] += 1
            img = b64(paths[r])
            cards.append(f'<figure class={cls}><img src="{img}">'
                         f'<figcaption>{lbl}<br>{sim[r].item():.3f}</figcaption></figure>')
        n_ok = sum(1 for r in top if gcode[r] == gcode[qi])

        # Every verified positive that did NOT make the top 10, with the rank it
        # actually got. The failures are the informative half: seeing the right
        # answer sitting at rank 4,000 says something different from it sitting
        # at rank 12, and neither is visible from a mAP number.
        order = torch.argsort(sim, descending=True)
        rank_of = torch.empty_like(order)
        rank_of[order] = torch.arange(len(order), device=dev)
        pos_idx = [int(j) for j in np.where(gcode == int(gcode[qi]))[0]
                   if Li[int(j)].item() != Li[qi].item()]
        missed = [(int(rank_of[j].item()) + 1, j) for j in pos_idx if j not in set(top)]
        missed.sort()
        mcards = []
        for rank, j in missed[:10]:
            img = b64(paths[j])
            if not img:
                continue
            band = "near" if rank <= 100 else ("far" if rank <= 5000 else "lost")
            mcards.append(f'<figure class="miss {band}"><img src="{img}">'
                          f'<figcaption>rank {rank:,}<br>{sim[j].item():.3f}</figcaption></figure>')
        miss_block = ""
        if mcards:
            miss_block = (f'<div class=lbl>missed positives &mdash; verified same product, '
                          f'not in the top 10 ({len(missed)} total)</div>'
                          f'<div class=r>{"".join(mcards)}</div>')

        rows.append(f'<section><h2>{g["brand"]} &middot; {g["model"]} &middot; '
                    f'{g["subcategory"]}<span class=m> {ann[int(gcode[qi])]["cell"]}'
                    f' &middot; {n_ok}/10 correct &middot; {len(pos_idx)} positives exist'
                    f'</span></h2>'
                    f'<div class=lbl>top 10 retrieved</div><div class=r>'
                    f'<figure class=q><img src="{b64(paths[qi])}">'
                    f'<figcaption>QUERY</figcaption></figure>{"".join(cards)}</div>'
                    f'{miss_block}</section>')

    html = f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>SigLIP2 retrievals</title>
<style>:root{{color-scheme:light dark;--bg:#f6f5f3;--card:#fff;--tx:#171717;--dim:#6f6f6f;
--line:#e4e1dc;--ok:#1e8e5a;--no:#c0392b;--jk:#b8860b;--q:#2d6cdf}}
@media(prefers-color-scheme:dark){{:root{{--bg:#121212;--card:#1d1d1d;--tx:#ececec;--dim:#9a9a9a;
--line:#2c2c2c;--ok:#35c98a;--no:#ff6b5c;--jk:#e0b050;--q:#6fa2ff}}}}
body{{margin:0;background:var(--bg);color:var(--tx);font:13px/1.5 ui-sans-serif,-apple-system,Arial}}
header{{padding:16px 20px;border-bottom:1px solid var(--line)}} h1{{margin:0;font-size:18px}}
.sub{{color:var(--dim);font-size:13px;margin-top:6px;max-width:80ch}}
section{{padding:12px 20px;border-bottom:1px solid var(--line)}}
h2{{margin:0 0 8px;font-size:13px}} .m{{color:var(--dim);font-weight:400}}
.r{{display:flex;gap:7px;overflow-x:auto;padding-bottom:4px}}
figure{{margin:0;flex:0 0 118px}}
figure img{{width:118px;height:118px;object-fit:cover;border-radius:7px;display:block;
border:3px solid var(--line);background:#fff}}
figure.q img{{border-color:var(--q)}} figure.ok img{{border-color:var(--ok)}}
figure.no img{{border-color:var(--no)}} figure.jk img{{border-color:var(--jk)}}
figcaption{{font-size:10.5px;color:var(--dim);text-align:center;margin-top:3px;line-height:1.25}}
figure.ok figcaption{{color:var(--ok);font-weight:650}}
figure.jk figcaption{{color:var(--jk)}}
.lbl{{font-size:11px;color:var(--dim);margin:8px 0 4px;text-transform:uppercase;
letter-spacing:.06em}}
figure.miss img{{border-style:dashed}}
figure.miss.near img{{border-color:var(--ok)}}
figure.miss.far img{{border-color:var(--jk)}}
figure.miss.lost img{{border-color:var(--no)}}
</style></head><body><header><h1>SigLIP2 SO400M-384 &mdash; top 10 per query</h1>
<div class=sub><b style="color:var(--q)">blue</b> query &middot;
<b style="color:var(--ok)">green</b> correct (verified, same group) &middot;
<b style="color:var(--jk)">amber</b> same group but never verified &mdash; ignored in scoring,
not counted as an error &middot; <b style="color:var(--no)">red</b> wrong, labelled with the
group it actually belongs to.<br>Below each row: <b>missed positives</b> (dashed) &mdash; verified
same-product photos the model did not put in the top 10, each with the rank it actually received.
Green dashed = rank &le;100, amber &le;5,000, red beyond. Scoring pool drops unlabelled listings of the query own brand, so everything shown is an item whose product we know.
Totals across this sample: {tally['ok']} correct, {tally['jk']} junk, {tally['no']} wrong.</div>
</header>{''.join(rows)}</body></html>"""
    open(OUT, "w", encoding="utf-8").write(html)
    print(f"{len(rows)} queries -> {OUT} ({os.path.getsize(OUT)/1024/1024:.1f} MB)")
    print(f"tally: {dict(tally)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
