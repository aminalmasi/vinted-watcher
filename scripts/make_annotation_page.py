"""A self-contained page for checking whether product groups are actually pure.

Measuring precision by eye over 100k listings is hopeless, but the groups are
the unit that matters: sample groups, show their photos together, and mark the
ones that do not belong. That converts an intractable pairwise task into a few
hundred quick judgements.

Groups are sampled STRATIFIED by size. A uniform sample would be dominated by
the long tail of 4-member groups and would say nothing about the big ones like
Oran (4,009), where an error costs the most.

Images are embedded, so the page works anywhere with no server and no paths.
Judgements are colour-blind by design: the instruction is same PRODUCT, and a
different colourway is explicitly correct.
"""

from __future__ import annotations

import base64, json, os, random, sys

DATA = os.path.expanduser("~/vestiaire_data")
GROUPS = f"{DATA}/ground_truth/groups.jsonl"
OUT = f"{DATA}/ground_truth/annotate.html"
N_GROUPS = int(os.environ.get("N_GROUPS", "40"))
PER_GROUP = int(os.environ.get("PER_GROUP", "12"))


def b64(rel):
    try:
        with open(os.path.join(DATA, rel), "rb") as fh:
            return "data:image/jpeg;base64," + base64.b64encode(fh.read()).decode()
    except OSError:
        return None


def main() -> int:
    gs = [json.loads(l) for l in open(GROUPS, encoding="utf-8")]
    gs = [g for g in gs if sum(1 for i in g["items"] if i["images"]) >= 4]
    random.seed(7)
    # stratify by size so big groups are not drowned out by the 4-member tail
    bands, chosen = {"small": [], "mid": [], "large": []}, []
    for g in gs:
        bands["large" if g["n"] >= 200 else "mid" if g["n"] >= 20 else "small"].append(g)
    per = max(1, N_GROUPS // 3)
    for k in ("small", "mid", "large"):
        chosen += random.sample(bands[k], min(per, len(bands[k])))
    random.shuffle(chosen)

    payload = []
    for g in chosen:
        items = [i for i in g["items"] if i["images"]]
        random.shuffle(items)
        cards = []
        for it in items[:PER_GROUP]:
            img = b64(it["images"][0])
            if img:
                cards.append({"id": it["id"], "img": img,
                              "price": (it.get("price") or 0) / 100,
                              "name": (it.get("name") or "")[:60]})
        if len(cards) >= 4:
            payload.append({"gid": g["group_id"], "brand": g["brand"],
                            "model": g["model"], "sub": g["subcategory"],
                            "n": g["n"], "cards": cards})

    html = TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False)
                            .replace("</", "<\\/"))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w", encoding="utf-8").write(html)
    n_img = sum(len(p["cards"]) for p in payload)
    print(f"{len(payload)} groups, {n_img} images -> {OUT} "
          f"({os.path.getsize(OUT)/1024/1024:.1f} MB)")
    return 0


TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Product group check</title><style>
:root{color-scheme:light dark;--bg:#f6f5f3;--card:#fff;--tx:#171717;--dim:#6f6f6f;
 --line:#e4e1dc;--bad:#c0392b;--ok:#1e8e5a}
@media(prefers-color-scheme:dark){:root{--bg:#121212;--card:#1d1d1d;--tx:#ececec;
 --dim:#9a9a9a;--line:#2c2c2c;--bad:#ff6b5c;--ok:#35c98a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);
 font:15px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}
header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;
 align-items:center;gap:16px;flex-wrap:wrap;position:sticky;top:0;background:var(--bg);z-index:5}
h1{margin:0;font-size:17px;font-weight:650}
.meta{color:var(--dim);font-size:13px}
.count{margin-left:auto;font-variant-numeric:tabular-nums;font-weight:650}
.hint{padding:10px 20px;color:var(--dim);font-size:13px;border-bottom:1px solid var(--line)}
.grid{display:grid;gap:14px;padding:18px 20px;
 grid-template-columns:repeat(auto-fill,minmax(160px,1fr))}
.c{background:var(--card);border:2px solid var(--line);border-radius:10px;
 overflow:hidden;cursor:pointer;position:relative}
.c img{width:100%;aspect-ratio:1;object-fit:cover;display:block}
.c.bad{border-color:var(--bad)}
.c.bad::after{content:"NOT THIS PRODUCT";position:absolute;inset:auto 0 0 0;
 background:var(--bad);color:#fff;font-size:11px;font-weight:700;text-align:center;padding:3px}
.lab{padding:6px 8px;font-size:11.5px;color:var(--dim)}
footer{padding:16px 20px;border-top:1px solid var(--line);display:flex;gap:10px;align-items:center}
button{background:var(--card);color:var(--tx);border:1px solid var(--line);
 border-radius:8px;padding:9px 18px;font:inherit;font-weight:600;cursor:pointer}
button.primary{background:var(--ok);color:#fff;border-color:var(--ok)}
textarea{width:100%;height:150px;font:12px ui-monospace,monospace;margin-top:10px;
 background:var(--card);color:var(--tx);border:1px solid var(--line);border-radius:8px;padding:10px}
</style></head><body>
<header><h1 id="t"></h1><span class="meta" id="m"></span><span class="count" id="c"></span></header>
<div class="hint">Click any photo that is <b>NOT the same product</b> as the majority.
A different <b>colour or material is still the same product</b> &mdash; leave those unmarked.
Keys: <b>&larr; &rarr;</b> move, <b>Enter</b> next.</div>
<div class="grid" id="g"></div>
<footer><button onclick="go(-1)">&larr; Prev</button>
<button class="primary" onclick="go(1)">Next &rarr;</button>
<button onclick="dump()">Show results</button></footer>
<div style="padding:0 20px 24px"><textarea id="out" placeholder="Results appear here when you press Show results — copy the whole box back to Claude."></textarea></div>
<script>const D=__DATA__;let i=0;const bad={};
function draw(){const g=D[i];document.getElementById('t').textContent=g.brand+" · "+g.model;
document.getElementById('m').textContent=g.sub+" · group of "+g.n;
document.getElementById('c').textContent=(i+1)+" / "+D.length;
document.getElementById('g').innerHTML=g.cards.map((c,k)=>
 '<div class="c'+((bad[g.gid]||[]).includes(c.id)?' bad':'')+'" data-k="'+k+'">'+
 '<img src="'+c.img+'"><div class="lab">'+(c.price?c.price.toFixed(0)+' €':'')+'</div></div>').join('');}
document.addEventListener('click',e=>{const el=e.target.closest('.c');if(!el)return;
 const g=D[i],c=g.cards[+el.dataset.k];bad[g.gid]=bad[g.gid]||[];
 const j=bad[g.gid].indexOf(c.id);j<0?bad[g.gid].push(c.id):bad[g.gid].splice(j,1);draw();});
function go(d){i=(i+d+D.length)%D.length;draw();}
document.addEventListener('keydown',e=>{if(e.key==='ArrowRight'||e.key==='Enter')go(1);
 if(e.key==='ArrowLeft')go(-1);});
function dump(){const r=D.map(g=>({gid:g.gid,brand:g.brand,model:g.model,sub:g.sub,
 shown:g.cards.length,bad:(bad[g.gid]||[]).length}));
 document.getElementById('out').value=JSON.stringify(r);}
draw();</script></body></html>"""


if __name__ == "__main__":
    sys.exit(main())
