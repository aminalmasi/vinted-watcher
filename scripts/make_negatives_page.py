"""See what the encoder actually confuses.

A mAP number says how often retrieval fails; this says WITH WHAT. Each row is
one group's query photo followed by its nearest different-brand neighbours, in
similarity order. Reading a few dozen of these tells you whether the failures
are reasonable (a black pump resembling another black pump) or diagnostic of
something wrong (matching on background, on the seller's carpet, on watermark).
"""
from __future__ import annotations
import base64, json, os, random, sys

DATA = os.path.expanduser("~/vestiaire_data")
SRC = f"{DATA}/ground_truth/hard_negatives.jsonl"
OUT = f"{DATA}/ground_truth/hard_negatives.html"
N_ROWS = int(os.environ.get("N_ROWS", "30"))
N_NEG = int(os.environ.get("N_NEG", "8"))


def b64(rel):
    try:
        with open(os.path.join(DATA, rel), "rb") as fh:
            return "data:image/jpeg;base64," + base64.b64encode(fh.read()).decode()
    except OSError:
        return None


def main() -> int:
    rows = [json.loads(l) for l in open(SRC, encoding="utf-8")]
    random.seed(3)
    rows = random.sample(rows, min(N_ROWS, len(rows)))
    blocks = []
    for r in rows:
        q = b64(r["query"])
        if not q:
            continue
        negs = []
        for n in r["negatives"][:N_NEG]:
            img = b64(n["path"])
            if img:
                negs.append(f'<figure><img src="{img}"><figcaption>{n["brand"]}'
                            f'<br><b>{n["sim"]:.3f}</b></figcaption></figure>')
        if negs:
            blocks.append(f'<section><h2>{r["group"]}</h2><div class="row">'
                          f'<figure class="q"><img src="{q}">'
                          f'<figcaption>QUERY</figcaption></figure>{"".join(negs)}'
                          f'</div></section>')
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hard negatives</title><style>
:root{{color-scheme:light dark;--bg:#f6f5f3;--card:#fff;--tx:#171717;--dim:#6f6f6f;
--line:#e4e1dc;--q:#b4442f}}
@media(prefers-color-scheme:dark){{:root{{--bg:#121212;--card:#1d1d1d;--tx:#ececec;
--dim:#9a9a9a;--line:#2c2c2c;--q:#ff7a5c}}}}
body{{margin:0;background:var(--bg);color:var(--tx);
font:14px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}}
header{{padding:18px 22px;border-bottom:1px solid var(--line)}}
h1{{margin:0 0 4px;font-size:19px}} .sub{{color:var(--dim);font-size:13px}}
section{{padding:14px 22px;border-bottom:1px solid var(--line)}}
h2{{margin:0 0 10px;font-size:14px;font-weight:650;color:var(--dim)}}
.row{{display:flex;gap:10px;overflow-x:auto;padding-bottom:4px}}
figure{{margin:0;flex:0 0 118px}}
figure img{{width:118px;height:118px;object-fit:cover;border-radius:8px;
border:2px solid var(--line);display:block;background:var(--card)}}
figure.q img{{border-color:var(--q)}}
figcaption{{font-size:11px;color:var(--dim);text-align:center;margin-top:4px;line-height:1.3}}
figure.q figcaption{{color:var(--q);font-weight:700}}
</style></head><body>
<header><h1>What the encoder confuses</h1>
<div class="sub">Each row: a product group's query photo (red) and its nearest
photos from <b>other brands</b>, with cosine similarity. Different brand means
these can never be the same product, so every one of them is a true negative
&mdash; the number says how close the encoder thought it was.</div></header>
{''.join(blocks)}</body></html>"""
    open(OUT, "w", encoding="utf-8").write(html)
    print(f"{len(blocks)} rows -> {OUT} ({os.path.getsize(OUT)/1024/1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
