"""Look at the view tags the model was LEAST sure about.

Median margin between the top two views is ~0.010, and 0.005 for front/top -
close enough to a coin flip that the labels could flip on a reworded prompt.
The useful thing to look at is therefore not a random sample but the low-margin
tail: if those are visibly wrong, zero-shot is not usable for view matching and
the same images are exactly the ones worth hand-labelling.

A high-margin block is included as a control. Without it, bad low-margin cases
are uninterpretable - they could mean the tagger is broken everywhere, or
working fine and honestly flagging its own uncertainty.
"""
from __future__ import annotations
import base64, collections, io, json, os, random, sys
import numpy as np
from PIL import Image

DATA = os.path.expanduser("~/vestiaire_data")
GT = f"{DATA}/ground_truth"
OUT = f"{GT}/view_check.html"
PER_VIEW = int(os.environ.get("PER_VIEW", "14"))


def b64(rel, size=132):
    try:
        with Image.open(os.path.join(DATA, rel)) as im:
            im = im.convert("RGB"); im.thumbnail((size, size))
            b = io.BytesIO(); im.save(b, "JPEG", quality=72)
        return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()
    except Exception:
        return None


def main() -> int:
    paths = json.load(open(f"{DATA}/embeddings/siglip2_paths.json"))
    tags = np.load(f"{GT}/view_tags.npy")
    marg = np.load(f"{GT}/view_margin.npy").astype(np.float32)
    names = json.load(open(f"{GT}/view_names.json"))
    print(f"{len(paths):,} photos, {len(names)} views")

    random.seed(9)
    blocks = []
    for band, lo, hi, note in (
            ("RISKY - lowest margin", 0.0, 0.25,
             "the tagger was barely able to choose; these are the ones to judge"),
            ("control - highest margin", 0.75, 1.0,
             "the tagger was confident; if these are also wrong, the problem is not uncertainty")):
        secs = []
        for vi, vname in enumerate(names):
            idx = np.where(tags == vi)[0]
            if len(idx) < 20:
                continue
            m = marg[idx]
            lo_q, hi_q = np.quantile(m, lo), np.quantile(m, max(hi, lo + 1e-6))
            sel = idx[(m >= lo_q) & (m <= hi_q)]
            if len(sel) == 0:
                continue
            pick = random.sample(list(sel), min(PER_VIEW, len(sel)))
            cards = []
            for j in pick:
                img = b64(paths[j])
                if img:
                    cards.append(f'<figure><img src="{img}">'
                                 f'<figcaption>{marg[j]:.4f}</figcaption></figure>')
            if cards:
                secs.append(f'<div class=v><div class=h>{vname}'
                            f'<span> n={len(idx):,} · median margin '
                            f'{np.median(m):.4f}</span></div>'
                            f'<div class=r>{"".join(cards)}</div></div>')
        blocks.append(f'<section><h2>{band}</h2><p class=note>{note}</p>'
                      f'{"".join(secs)}</section>')

    html = f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>View tags</title>
<style>:root{{color-scheme:light dark;--bg:#f6f5f3;--card:#fff;--tx:#171717;--dim:#6f6f6f;
--line:#e4e1dc;--bad:#c0392b;--ok:#1e8e5a}}
@media(prefers-color-scheme:dark){{:root{{--bg:#121212;--card:#1d1d1d;--tx:#ececec;
--dim:#9a9a9a;--line:#2c2c2c;--bad:#ff6b5c;--ok:#35c98a}}}}
body{{margin:0;background:var(--bg);color:var(--tx);font:13px/1.5 ui-sans-serif,-apple-system,Arial}}
header{{padding:16px 20px;border-bottom:1px solid var(--line)}} h1{{margin:0;font-size:18px}}
.sub{{color:var(--dim);font-size:13px;margin-top:6px;max-width:84ch}}
section{{padding:10px 20px 18px;border-bottom:2px solid var(--line)}}
h2{{font-size:14px;margin:12px 0 2px}} .note{{color:var(--dim);margin:0 0 10px;font-size:12px}}
.v{{margin-bottom:12px}} .h{{font-size:12.5px;font-weight:650;margin-bottom:5px}}
.h span{{color:var(--dim);font-weight:400}}
.r{{display:flex;gap:6px;overflow-x:auto;padding-bottom:4px}}
figure{{margin:0;flex:0 0 130px}}
figure img{{width:130px;height:130px;object-fit:cover;border-radius:7px;
border:1px solid var(--line);display:block;background:#fff}}
figcaption{{font-size:10px;color:var(--dim);text-align:center;margin-top:2px}}
</style></head><body><header><h1>Zero-shot view tags &mdash; the uncertain ones</h1>
<div class=sub>Each row is one predicted view; the number under a photo is the
<b>margin</b> between the top two views. The first block is the bottom quartile of margin
per view (the tagger nearly could not choose), the second is the top quartile as a control.
Judge: are the RISKY ones actually mislabelled, and are the control ones right? If both are
wrong, zero-shot view detection is not usable here and these images become the hand-labelling
set instead.</div></header>{''.join(blocks)}</body></html>"""
    open(OUT, "w", encoding="utf-8").write(html)
    print(f"wrote {OUT} ({os.path.getsize(OUT)/1024/1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
