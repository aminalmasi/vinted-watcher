"""Annotation pages that BUILD a test set, not just estimate purity.

Difference from the earlier 39-group page: every image shown here becomes part
of the evaluation set if it survives review, so the verification has to cover
the exact images we will use rather than a sample standing in for them.

Sampling is stratified on two axes at once - group SIZE and visual COHERENCE -
because both drive difficulty and a single-axis sample would leave one of them
unrepresented. Coherence below ~0.95 means the model name does not pin down
appearance (Fifi in every colour and heel height); above ~1.15 it does (Dior
B23). A test set drawn only from the tight ones would flatter every encoder.

Split across pages because a single file of this size fails to upload.

Output per group: which photos were rejected, or whether the whole group was
skipped as unclear. Anything skipped is excluded rather than guessed at.
"""
from __future__ import annotations
import base64, io, json, os, random, sys
from PIL import Image

DATA = os.path.expanduser("~/vestiaire_data")
OUT = f"{DATA}/ground_truth"
N_GROUPS = int(os.environ.get("N_GROUPS", "120"))
PER_GROUP = int(os.environ.get("PER_GROUP", "14"))
PAGES = int(os.environ.get("PAGES", "2"))
THUMB = 150


def b64(rel):
    try:
        with Image.open(os.path.join(DATA, rel)) as im:
            im = im.convert("RGB"); im.thumbnail((THUMB, THUMB))
            b = io.BytesIO(); im.save(b, "JPEG", quality=68)
        return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()
    except Exception:
        return None


def main() -> int:
    gs = {g["group_id"]: g for g in
          (json.loads(l) for l in open(f"{OUT}/groups.jsonl", encoding="utf-8"))}
    integ = {r["group_id"]: r for r in json.load(open(f"{OUT}/group_integrity.json"))}

    elig = []
    for gid, r in integ.items():
        g = gs[gid]
        withimg = [it for it in g["items"] if it.get("images")]
        if len(withimg) >= 6:
            elig.append((r["coh"], r["n"], gid, withimg))
    random.seed(17)
    # stratify on coherence x size so neither axis is under-sampled
    cells = {}
    for coh, n, gid, wi in elig:
        ck = "tight" if coh >= 1.15 else ("mid" if coh >= 0.95 else "loose")
        sk = "big" if n >= 200 else ("mid" if n >= 30 else "small")
        cells.setdefault((ck, sk), []).append((gid, wi, coh, n))
    per_cell = max(1, N_GROUPS // max(len(cells), 1))
    chosen = []
    for k, v in sorted(cells.items()):
        chosen += [(k,) + x for x in random.sample(v, min(per_cell, len(v)))]
    random.shuffle(chosen)
    print(f"{len(cells)} strata, {len(chosen)} groups selected")

    payload = []
    for (cell, gid, wi, coh, n) in chosen:
        random.shuffle(wi)
        cards = []
        for it in wi[:PER_GROUP]:
            img = b64(it["images"][0])
            if img:
                cards.append({"id": it["id"], "img": img,
                              "price": (it.get("price") or 0) / 100})
        if len(cards) >= 6:
            payload.append({"gid": gid, "brand": gs[gid]["brand"],
                            "model": gs[gid]["model"], "sub": gs[gid]["subcategory"],
                            "n": n, "coh": round(coh, 2),
                            "cell": "/".join(cell), "cards": cards})

    size = (len(payload) + PAGES - 1) // PAGES
    files = []
    for p in range(PAGES):
        chunk = payload[p*size:(p+1)*size]
        if not chunk:
            continue
        path = f"{OUT}/testset_page{p+1}.html"
        open(path, "w", encoding="utf-8").write(
            TEMPLATE.replace("__DATA__", json.dumps(chunk, ensure_ascii=False)
                             .replace("</", "<\\/"))
                    .replace("__PAGE__", f"{p+1} of {PAGES}")
                    .replace("__PAGE_N__", str(p+1)))
        files.append((path, len(chunk), os.path.getsize(path)/1024/1024))
    for f, k, mb in files:
        print(f"  {f}  {k} groups  {mb:.1f} MB")
    return 0


TEMPLATE = r"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Build the test set</title><style>
:root{color-scheme:light dark;--bg:#f6f5f3;--card:#fff;--tx:#171717;--dim:#6f6f6f;
--line:#e4e1dc;--bad:#c0392b;--ok:#1e8e5a;--warn:#b8860b}
@media(prefers-color-scheme:dark){:root{--bg:#121212;--card:#1d1d1d;--tx:#ececec;
--dim:#9a9a9a;--line:#2c2c2c;--bad:#ff6b5c;--ok:#35c98a;--warn:#e0b050}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);
font:15px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}
header{padding:12px 18px;border-bottom:1px solid var(--line);display:flex;gap:14px;
align-items:center;flex-wrap:wrap;position:sticky;top:0;background:var(--bg);z-index:5}
h1{margin:0;font-size:16px;font-weight:650}
.meta{color:var(--dim);font-size:12.5px}
.count{margin-left:auto;font-variant-numeric:tabular-nums;font-weight:650}
.bar{height:3px;background:var(--line)}.bar i{display:block;height:3px;background:var(--ok)}
.hint{padding:9px 18px;color:var(--dim);font-size:12.5px;border-bottom:1px solid var(--line)}
.grid{display:grid;gap:12px;padding:16px 18px;
grid-template-columns:repeat(auto-fill,minmax(140px,1fr))}
.c{background:var(--card);border:2px solid var(--line);border-radius:9px;overflow:hidden;
cursor:pointer;position:relative}
.c img{width:100%;aspect-ratio:1;object-fit:cover;display:block}
.c.bad{border-color:var(--bad)}
.c.bad::after{content:"NOT THIS";position:absolute;inset:auto 0 0 0;background:var(--bad);
color:#fff;font-size:10.5px;font-weight:700;text-align:center;padding:2px}
footer{padding:14px 18px;border-top:1px solid var(--line);display:flex;gap:9px;
align-items:center;flex-wrap:wrap}
button{background:var(--card);color:var(--tx);border:1px solid var(--line);border-radius:8px;
padding:9px 16px;font:inherit;font-weight:600;cursor:pointer}
button.primary{background:var(--ok);color:#fff;border-color:var(--ok)}
button.skip{border-color:var(--warn);color:var(--warn)}
.done{color:var(--warn);font-size:12.5px}
textarea{width:100%;height:120px;font:12px ui-monospace,monospace;margin-top:8px;
background:var(--card);color:var(--tx);border:1px solid var(--line);border-radius:8px;padding:9px}
</style></head><body>
<header><h1 id=t></h1><span class=meta id=m></span><span class=count id=c></span></header>
<div class=bar><i id=bar style="width:0"></i></div>
<div class=hint>Click any photo that is <b>NOT the same product</b> as the rest.
Different <b>colour, heel height or material is still the same product</b> &mdash; leave those.
If a group is unclear, press <b>Skip</b> and it is excluded rather than guessed.
Keys: <b>&rarr;</b>/<b>Enter</b> next, <b>&larr;</b> back, <b>s</b> skip.</div>
<div class=grid id=g></div>
<footer><button onclick="go(-1)">&larr; Back</button>
<button class=primary onclick="go(1)">Next &rarr;</button>
<button class=skip onclick="skip()">Skip (unclear)</button>
<button onclick="save()">&darr; Download labels</button>
<button onclick="dump()">Show as text</button>
<span class=done id=done></span><span class=meta id=auto></span></footer>
<div style="padding:0 18px 22px"><textarea id=out
placeholder="Press Show results when finished, then paste this whole box back to Claude."></textarea></div>
<script>const D=__DATA__;let i=0;const bad={},skipped={};
function draw(){const g=D[i];
 document.getElementById('t').textContent=g.brand+" · "+g.model;
 document.getElementById('m').textContent=g.sub+" · "+g.n+" listings · coherence "+g.coh+" · "+g.cell;
 document.getElementById('c').textContent=(i+1)+" / "+D.length+"  (page __PAGE__)";
 document.getElementById('bar').style.width=(100*(i+1)/D.length)+"%";
 document.getElementById('done').textContent=skipped[g.gid]?"skipped":"";
 document.getElementById('g').innerHTML=g.cards.map((c,k)=>
  '<div class="c'+((bad[g.gid]||[]).includes(c.id)?' bad':'')+'" data-k="'+k+'">'+
  '<img src="'+c.img+'"></div>').join('');}
document.addEventListener('click',e=>{const el=e.target.closest('.c');if(!el)return;
 const g=D[i],c=g.cards[+el.dataset.k];bad[g.gid]=bad[g.gid]||[];
 const j=bad[g.gid].indexOf(c.id);j<0?bad[g.gid].push(c.id):bad[g.gid].splice(j,1);draw();persist();});
function go(d){i=(i+d+D.length)%D.length;draw();persist();}
function skip(){skipped[D[i].gid]=1;persist();go(1);}
document.addEventListener('keydown',e=>{if(e.key==='ArrowRight'||e.key==='Enter')go(1);
 if(e.key==='ArrowLeft')go(-1); if(e.key==='s')skip();});
function results(){return D.map(g=>({gid:g.gid,model:g.model,cell:g.cell,
 shown:g.cards.length,bad:(bad[g.gid]||[]),skipped:!!skipped[g.gid]}));}
function dump(){document.getElementById('out').value=JSON.stringify(results());}
function save(){
 // A download is the obvious way to hand this back, and its absence was an
 // oversight. Kept alongside the text box because some preview panes block
 // file downloads - opening the .html directly in a browser always works.
 const blob=new Blob([JSON.stringify(results(),null,1)],{type:'application/json'});
 const a=document.createElement('a');
 a.href=URL.createObjectURL(blob); a.download='labels_page__PAGE_N__.json';
 document.body.appendChild(a); a.click(); a.remove();
 setTimeout(()=>URL.revokeObjectURL(a.href),2000);
 dump();
}
// Autosave, so a refresh or a closed tab never costs the work again.
const KEY='vc_labels___PAGE_N__';
function persist(){try{localStorage.setItem(KEY,JSON.stringify({bad,skipped,i}));
 document.getElementById('auto').textContent='saved';}catch(e){}}
(function restore(){try{const r=JSON.parse(localStorage.getItem(KEY)||'null');
 if(r){Object.assign(bad,r.bad||{});Object.assign(skipped,r.skipped||{});
  i=r.i||0;document.getElementById('auto').textContent='restored previous session';}
 }catch(e){}})();
draw();</script></body></html>"""


if __name__ == "__main__":
    sys.exit(main())
