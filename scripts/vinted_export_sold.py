#!/usr/bin/env python3
"""Build a browsable folder of the sold items: photos, metadata, and a gallery.

The state file is a 20MB JSON blob and the photos are a flat directory of
{id}.webp - fine for code, useless for judging by eye. This assembles the two
into one place so the sold set can actually be looked at: a contact-sheet page
with every sale, sortable, plus csv and jsonl for anything else.

Photos are SYMLINKED, not copied. The set is over a gigabyte and it changes
every cycle; a copy would double the storage and be stale immediately.

Deliberately covers the archive too, under a flag. Deletions run at roughly
twice the volume of confirmed sales and an unknown share of them ARE sales, so
being able to eyeball them side by side is the only way to form a view on
whether they look like the same population.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES = "/extra/malmasik/vinted_images"
OUT = "/extra/malmasik/vinted_sold"


def state() -> dict:
    """Read committed state, never the working tree.

    A stale working-tree copy of this file once got swept into a commit and
    reverted 37 confirmed sales, so this reads from git instead.
    """
    out = subprocess.run(["git", "show", "origin/main:data/vinted_track.json"],
                         cwd=REPO, capture_output=True, check=True)
    return json.loads(out.stdout)


def rows(st: dict, which: str) -> list[dict]:
    src = st["sold"] if which == "sold" else st["archive"]
    out = []

    def num(x):
        # prices arrive as strings like "167.00"
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    for iid, v in src.items():
        if which == "deleted" and v.get("final") != "deleted":
            continue
        if which == "aged" and v.get("final") != "aged_out":
            continue
        days = v.get("age_at_sale")
        if days is None:
            days = v.get("age_at_end")
        out.append({
            "id": iid,
            "brand": v.get("brand") or "?",
            "price": num(v.get("price")),
            "days_to_sell": days,
            "final": v.get("final") or which,
            "seen_h": round((v.get("sold_seen", v.get("at", 0))
                             - v.get("first_seen", 0)) / 3600, 1)
            if v.get("first_seen") else None,
            "url": f"https://www.vinted.it/items/{iid}-{v.get('slug','')}",
            "photo": f"{iid}.webp" if os.path.exists(
                os.path.join(IMAGES, f"{iid}.webp")) else None,
        })
    out.sort(key=lambda r: (r["brand"], -(r["price"] or 0)))
    return out


def gallery(rs: list[dict], which: str, path: str) -> None:
    cards = []
    for r in rs:
        img = (f'<img loading="lazy" src="photos/{r["photo"]}" alt="">'
               if r["photo"] else '<div class="nophoto">no photo</div>')
        d = r["days_to_sell"]
        # days-to-sell is the interesting field, so make its absence visible
        # rather than rendering a confident-looking blank
        dur = f'{d:.1f}d' if isinstance(d, (int, float)) else '<span class="unk">unknown</span>'
        cards.append(
            f'<a class="card" href="{html.escape(r["url"])}" target="_blank" '
            f'data-brand="{html.escape(r["brand"])}" '
            f'data-price="{r["price"] or 0}" data-days="{d if isinstance(d,(int,float)) else -1}">'
            f'{img}<div class="meta"><b>{html.escape(r["brand"])}</b>'
            f'<span>{r["price"] or "?"} EUR</span>'
            f'<span class="dur">{dur}</span></div></a>')

    brands = sorted({r["brand"] for r in rs})
    opts = "".join(f'<option>{html.escape(b)}</option>' for b in brands)
    withdur = sum(1 for r in rs if isinstance(r["days_to_sell"], (int, float)))
    doc = f"""<!doctype html><meta charset="utf-8">
<title>Vinted {which} - {len(rs)} items</title>
<style>
 body{{font:14px/1.4 system-ui,sans-serif;margin:0;padding:16px;background:#111;color:#eee}}
 h1{{font-size:18px;margin:0 0 4px}}
 .sub{{color:#999;margin-bottom:14px}}
 .bar{{position:sticky;top:0;background:#111;padding:8px 0 12px;z-index:5}}
 select,input,button{{background:#222;color:#eee;border:1px solid #444;padding:5px 8px;border-radius:4px}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}}
 .card{{background:#1b1b1b;border-radius:6px;overflow:hidden;text-decoration:none;color:inherit;border:1px solid #2a2a2a}}
 .card img{{width:100%;aspect-ratio:3/4;object-fit:cover;display:block;background:#000}}
 .nophoto{{aspect-ratio:3/4;display:grid;place-items:center;color:#666;background:#161616}}
 .meta{{padding:6px 8px;display:flex;flex-direction:column;gap:1px}}
 .meta span{{color:#bbb;font-size:12px}}
 .dur{{color:#7ec699!important}}
 .unk{{color:#c67e7e}}
</style>
<h1>Vinted {which}: {len(rs)} items</h1>
<div class="sub">{withdur} have a true upload-to-sale duration; the rest were
confirmed before that field was recorded. Click any card for the listing.</div>
<div class="bar">
 <select id="b"><option value="">all brands</option>{opts}</select>
 <select id="s">
  <option value="brand">sort: brand</option>
  <option value="price">sort: price high to low</option>
  <option value="days">sort: fastest sold first</option>
 </select>
 <input id="q" placeholder="max days" size="8">
 <span id="n" style="color:#999;margin-left:8px"></span>
</div>
<div class="grid" id="g">{''.join(cards)}</div>
<script>
const g=document.getElementById('g'),all=[...g.children];
function draw(){{
 const b=document.getElementById('b').value,s=document.getElementById('s').value,
       q=parseFloat(document.getElementById('q').value);
 let v=all.filter(c=>(!b||c.dataset.brand===b)&&
   (isNaN(q)||(+c.dataset.days>=0&&+c.dataset.days<=q)));
 if(s==='price')v.sort((a,b)=>b.dataset.price-a.dataset.price);
 if(s==='days')v.sort((a,b)=>{{const x=+a.dataset.days,y=+b.dataset.days;
   if(x<0)return 1;if(y<0)return -1;return x-y}});
 if(s==='brand')v.sort((a,b)=>a.dataset.brand.localeCompare(b.dataset.brand)||
   b.dataset.price-a.dataset.price);
 g.replaceChildren(...v);
 document.getElementById('n').textContent=v.length+' shown';
}}
['b','s','q'].forEach(i=>document.getElementById(i).addEventListener('input',draw));
draw();
</script>"""
    open(path, "w").write(doc)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--which", default="sold",
                    choices=["sold", "deleted", "aged"],
                    help="sold = confirmed sales; deleted = 404'd, some of "
                         "which are probably sales; aged = over 6 months")
    a = ap.parse_args()

    st = state()
    rs = rows(st, a.which)
    os.makedirs(OUT, exist_ok=True)
    link = os.path.join(OUT, "photos")
    if not os.path.islink(link) and not os.path.exists(link):
        os.symlink(IMAGES, link)

    base = os.path.join(OUT, a.which)
    with open(base + ".csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rs[0].keys()))
        w.writeheader()
        w.writerows(rs)
    with open(base + ".jsonl", "w") as fh:
        for r in rs:
            fh.write(json.dumps(r) + "\n")
    gallery(rs, a.which, base + ".html")

    havephoto = sum(1 for r in rs if r["photo"])
    havedur = sum(1 for r in rs if isinstance(r["days_to_sell"], (int, float)))
    print(f"{a.which}: {len(rs)} items, {havephoto} with photo, "
          f"{havedur} with true duration")
    print(f"  {base}.html   <- open this")
    print(f"  {base}.csv")
    print(f"  {base}.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
