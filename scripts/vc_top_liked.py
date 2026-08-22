"""The most-liked live listings, as a self-contained HTML page.

There is no sort-by-likes, so "the 20 most liked on the site" is not something
the API will hand over — it has to be found by sampling and ranking locally.
Two things make the sample much better than naively taking the newest listings:

  * a PRICE FLOOR (EUR 200 here), which is what was asked for; and
  * AGE BANDS. Likes accumulate over time, so the newest listings always look
    unpopular. If the API accepts an upper bound on createdAt we sample older
    live listings too — items that have been on sale for months and are still
    collecting favourites are exactly where the like-magnets are. The script
    verifies `lte` actually works before relying on it, and says so either way.

Images are embedded as data URIs rather than hotlinked: the page then works
offline, and it does not lean on Vestiaire's CDN to display someone else's
photos. They are fetched small (w=400) to keep the file a couple of MB.

Runs on GitHub Actions — search and images both 403 the university IP.
"""

from __future__ import annotations

import base64
import html
import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vestiaire.client import FIELDS, LOCALE, PAGE, SEARCH, UA   # noqa: E402
from vestiaire.run import BRANDS, SHOES_WOMEN                   # noqa: E402

FLOOR_CENTS = int(os.environ.get("VC_FLOOR_CENTS", "20000"))    # EUR 200
TOP_N = int(os.environ.get("VC_TOP", "20"))
PAGES_PER_BAND = int(os.environ.get("VC_PAGES", "4"))
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "top_liked.html")
PHOTOS = int(os.environ.get("VC_PHOTOS", "1"))      # photos embedded per item
WIDTH = int(os.environ.get("VC_WIDTH", "400"))      # requested image width
LAYOUT = os.environ.get("VC_LAYOUT", "grid")        # grid | viewer
IMG = ("https://images.vestiairecollective.com/images/resized/"
       "w={w},q=75/produit/{p}")

log = logging.getLogger("top")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9",
                  "Origin": "https://www.vestiairecollective.com",
                  "Referer": "https://www.vestiairecollective.com/",
                  "x-usecase": "catalog", "Content-Type": "application/json"})
STOP = False


def search(brand, offset, gte, lte=None, limit=PAGE):
    global STOP
    if STOP:
        return None
    time.sleep(random.uniform(6, 10))
    created = {"gte": gte} | ({"lte": lte} if lte else {})
    body = {"pagination": {"offset": offset, "limit": limit}, "fields": FIELDS,
            "filters": {"brand.id": [brand], "categoryLvl0.id": [SHOES_WOMEN],
                        "sold": False, "price": {"gte": FLOOR_CENTS},
                        "createdAt": created},
            "locale": LOCALE, "sort": "recency"}
    try:
        r = S.post(SEARCH, json=body, timeout=45)
    except requests.RequestException as exc:
        log.warning("%s", type(exc).__name__)
        return None
    if r.status_code == 429:
        STOP = True
        log.warning("429 — stopping")
        return None
    if r.status_code != 200:
        return None
    return r.json()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    now = int(time.time())

    # Does the API honour an upper bound on createdAt? If it does we can reach
    # the older, more-liked stock; if not, we are stuck with recent listings and
    # the page should say so rather than pretend otherwise.
    def hits(j):
        return (j or {}).get("paginationStats", {}).get("totalHits")
    # Probe on a small brand over a short window: totalHits saturates at 10000,
    # so a wide probe compares two capped numbers and learns nothing.
    probe_all = search("115", 0, now - 30*86400, limit=1)
    probe_cut = search("115", 0, now - 30*86400, lte=now - 15*86400, limit=1)
    a, b = hits(probe_all), hits(probe_cut)
    lte_works = bool(a and b and a < 10000 and 0 < b < a)
    log.info("createdAt lte supported: %s  (30d=%s, 30d capped at 15d=%s)",
             lte_works, a, b)

    bands = ([(now - 365*86400, now - 90*86400, "3-12 months"),
              (now - 90*86400, None, "under 3 months")] if lte_works
             else [(now - 365*86400, None, "any age")])

    rows = []
    for bid, name in BRANDS.items():
        for gte, lte, band in bands:
            for p in range(PAGES_PER_BAND):
                j = search(bid, p * PAGE, gte, lte)
                items = (j or {}).get("items") or []
                if not items:
                    break
                for it in items:
                    if it.get("likes") is None:
                        continue
                    pics = it.get("pictures") or []
                    paths = []
                    for q in pics[:PHOTOS]:
                        if isinstance(q, dict):
                            q = q.get("path") or q.get("url")
                        if q:
                            paths.append(q)
                    rows.append({
                        "id": str(it.get("id")), "name": it.get("name") or "",
                        "brand": (it.get("brand") or {}).get("name") or name,
                        "price": (it.get("price") or {}).get("cents", 0) / 100,
                        "likes": it["likes"], "created": it.get("createdAt"),
                        "pics": paths,
                        "url": "https://www.vestiairecollective.com" + (it.get("link") or ""),
                    })
        log.info("%-20s pool now %d", name, len(rows))

    if not rows:
        log.error("nothing sampled")
        return 1

    seen, uniq = set(), []
    for r in sorted(rows, key=lambda r: -r["likes"]):
        if r["id"] in seen:
            continue
        seen.add(r["id"]); uniq.append(r)
    top = uniq[:TOP_N]
    log.info("sampled %d listings; top like count %d, cut-off %d",
             len(uniq), top[0]["likes"], top[-1]["likes"])

    # The image host throttles just like the search API: at ~1 req/s the first
    # nine came back and ranks 10-20 were all refused, a contiguous tail rather
    # than scattered failures. So fetch photos at the same unhurried pace as
    # everything else, retry once, and SAY when one is missing instead of
    # silently rendering a grey box.
    want = sum(len(r["pics"]) for r in top)
    log.info("fetching %d photos for %d items", want, len(top))
    for i, r in enumerate(top, 1):
        r["imgs"] = []
        for path in r["pics"]:
            src = path if str(path).startswith("http") else IMG.format(w=WIDTH, p=path)
            for attempt in range(2):
                time.sleep(random.uniform(6, 10) if attempt == 0 else 30)
                try:
                    ir = S.get(src, timeout=45,
                               headers={"Referer": "https://www.vestiairecollective.com/",
                                        "Accept": "image/avif,image/webp,*/*"})
                except requests.RequestException as exc:
                    log.warning("#%d photo: %s", i, type(exc).__name__)
                    continue
                if ir.status_code == 200 and ir.content:
                    mime = ir.headers.get("content-type", "image/jpeg").split(";")[0]
                    r["imgs"].append(f"data:{mime};base64,"
                                     + base64.b64encode(ir.content).decode())
                    break
                log.warning("#%d photo: HTTP %d%s", i, ir.status_code,
                            " (retrying)" if attempt == 0 else " - giving up")
        r["img"] = r["imgs"][0] if r["imgs"] else None
    got = sum(len(r["imgs"]) for r in top)
    log.info("embedded %d/%d photos; %d/%d items have at least one",
             got, want, sum(1 for r in top if r["imgs"]), len(top))

    page = (render_viewer(top, len(uniq), lte_works) if LAYOUT == "viewer"
            else render(top, len(uniq), lte_works))
    open(OUT, "w", encoding="utf-8").write(page)
    log.info("wrote %s (%.1f MB)", OUT, os.path.getsize(OUT)/1024/1024)
    return 0


def render(top, pool, lte_works) -> str:
    e = lambda v: html.escape(str(v))
    cards = []
    for i, r in enumerate(top, 1):
        when = (datetime.fromtimestamp(r["created"], timezone.utc).strftime("%d %b %Y")
                if r.get("created") else "—")
        age = (f"{(time.time()-r['created'])/86400:.0f} giorni fa"
               if r.get("created") else "")
        img = (f'<img src="{r["img"]}" alt="{e(r["name"])}" loading="lazy">'
               if r.get("img") else '<div class="noimg">nessuna immagine</div>')
        cards.append(f"""
      <a class="card" href="{e(r['url'])}" target="_blank" rel="noopener">
        <div class="rank">{i}</div>
        <div class="ph">{img}</div>
        <div class="body">
          <div class="brand">{e(r['brand'])}</div>
          <div class="name">{e(r['name'])}</div>
          <div class="meta">
            <span class="price">{r['price']:,.0f} €</span>
            <span class="likes">{r['likes']} ❤</span>
          </div>
          <div class="date">{e(when)}<span class="age">{e(age)}</span></div>
        </div>
      </a>""")
    note = ("campione: annunci di 3-12 mesi e sotto i 3 mesi"
            if lte_works else
            "campione: solo annunci recenti (il filtro per data massima non è supportato)")
    return f"""<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vestiaire — Top {len(top)} per like</title>
<style>
:root {{ color-scheme: light dark;
  --bg:#faf9f7; --card:#fff; --tx:#1a1a1a; --dim:#6b6b6b; --line:#e6e3de; --accent:#b4442f; }}
@media (prefers-color-scheme: dark) {{ :root {{
  --bg:#141414; --card:#1e1e1e; --tx:#ececec; --dim:#9a9a9a; --line:#2e2e2e; --accent:#ff7a5c; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--tx);
  font:15px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }}
header {{ padding:32px 24px 12px; max-width:1180px; margin:0 auto; }}
h1 {{ margin:0 0 6px; font-size:26px; letter-spacing:-.02em; font-weight:650; }}
.sub {{ color:var(--dim); font-size:13.5px; }}
.grid {{ max-width:1180px; margin:0 auto; padding:20px 24px 56px;
  display:grid; gap:18px; grid-template-columns:repeat(auto-fill,minmax(228px,1fr)); }}
.card {{ position:relative; background:var(--card); border:1px solid var(--line);
  border-radius:12px; overflow:hidden; text-decoration:none; color:inherit;
  display:flex; flex-direction:column; transition:transform .12s ease, box-shadow .12s ease; }}
.card:hover {{ transform:translateY(-2px); box-shadow:0 6px 20px rgba(0,0,0,.12); }}
.rank {{ position:absolute; top:10px; left:10px; z-index:2; background:var(--accent);
  color:#fff; font-size:12px; font-weight:700; min-width:24px; height:24px;
  border-radius:12px; display:grid; place-items:center; padding:0 7px; }}
.ph {{ aspect-ratio:1/1; background:var(--bg); display:grid; place-items:center; overflow:hidden; }}
.ph img {{ width:100%; height:100%; object-fit:cover; display:block; }}
.noimg {{ color:var(--dim); font-size:12px; }}
.body {{ padding:12px 13px 14px; display:flex; flex-direction:column; gap:5px; flex:1; }}
.brand {{ font-size:11px; letter-spacing:.09em; text-transform:uppercase; color:var(--dim); }}
.name {{ font-size:13.5px; line-height:1.35; min-height:2.7em; }}
.meta {{ display:flex; justify-content:space-between; align-items:baseline;
  margin-top:auto; padding-top:6px; }}
.price {{ font-weight:650; font-size:15px; }}
.likes {{ color:var(--accent); font-weight:650; font-size:13.5px; }}
.date {{ font-size:11.5px; color:var(--dim); display:flex; justify-content:space-between; }}
.age {{ opacity:.75; }}
footer {{ max-width:1180px; margin:0 auto; padding:0 24px 40px; color:var(--dim); font-size:12px; }}
</style></head><body>
<header>
  <h1>I {len(top)} articoli più desiderati</h1>
  <div class="sub">Scarpe donna sopra i {FLOOR_CENTS//100} €, ancora in vendita ·
    {pool:,} annunci esaminati · {note} ·
    aggiornato {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}</div>
</header>
<div class="grid">{''.join(cards)}
</div>
<footer>I like sono l'unico segnale pubblico di domanda: Vestiaire tiene private le offerte.
Non esiste un ordinamento per like, quindi questa è la classifica di un campione, non dell'intero sito.</footer>
</body></html>"""


VIEWER_CSS = """
:root{color-scheme:light dark;--bg:#f6f5f3;--panel:#fff;--tx:#171717;--dim:#6f6f6f;
 --line:#e4e1dc;--accent:#b4442f;--shadow:0 8px 30px rgba(0,0,0,.10)}
@media (prefers-color-scheme:dark){:root{--bg:#111;--panel:#1c1c1c;--tx:#ededed;
 --dim:#9b9b9b;--line:#2c2c2c;--accent:#ff7a5c;--shadow:0 8px 30px rgba(0,0,0,.5)}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.55 ui-sans-serif,
 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;overflow:hidden}
.wrap{height:100vh;display:grid;grid-template-rows:auto 1fr auto}
.top{display:flex;align-items:center;gap:14px;padding:12px 20px;border-bottom:1px solid var(--line)}
.counter{font-variant-numeric:tabular-nums;font-weight:650}
.counter b{color:var(--accent);font-size:19px}
.title{color:var(--dim);font-size:13px;margin-left:auto;text-align:right}
.stage{display:grid;grid-template-columns:1fr 340px;gap:0;min-height:0}
.shot{position:relative;display:grid;place-items:center;padding:18px;min-height:0;background:var(--bg)}
.shot img{max-width:100%;max-height:100%;object-fit:contain;border-radius:10px;box-shadow:var(--shadow)}
.thumbs{position:absolute;bottom:16px;left:50%;transform:translateX(-50%);display:flex;gap:8px}
.thumbs img{width:52px;height:52px;object-fit:cover;border-radius:7px;cursor:pointer;
 border:2px solid transparent;opacity:.6;box-shadow:var(--shadow)}
.thumbs img.on{border-color:var(--accent);opacity:1}
.side{border-left:1px solid var(--line);background:var(--panel);padding:26px 24px;
 overflow-y:auto;display:flex;flex-direction:column;gap:16px}
.brand{font-size:11.5px;letter-spacing:.11em;text-transform:uppercase;color:var(--dim)}
h1{margin:0;font-size:21px;line-height:1.3;font-weight:600;letter-spacing:-.01em}
.big{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
.price{font-size:30px;font-weight:660;letter-spacing:-.02em}
.likes{font-size:17px;font-weight:650;color:var(--accent)}
dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:8px 16px;font-size:13.5px;
 border-top:1px solid var(--line);padding-top:16px}
dt{color:var(--dim)} dd{margin:0;text-align:right;font-variant-numeric:tabular-nums}
.go{margin-top:auto;display:block;text-align:center;background:var(--accent);color:#fff;
 text-decoration:none;padding:12px;border-radius:9px;font-weight:600;font-size:14px}
.bottom{display:flex;align-items:center;justify-content:center;gap:10px;padding:12px;
 border-top:1px solid var(--line)}
button{background:var(--panel);color:var(--tx);border:1px solid var(--line);border-radius:8px;
 padding:9px 20px;font:inherit;font-weight:600;cursor:pointer}
button:hover{border-color:var(--accent);color:var(--accent)}
.hint{color:var(--dim);font-size:12px;margin-left:12px}
.noimg{color:var(--dim)}
@media (max-width:820px){.stage{grid-template-columns:1fr;grid-template-rows:1fr auto}
 .side{border-left:none;border-top:1px solid var(--line)}body{overflow:auto}.wrap{height:auto}}
"""

VIEWER_JS = """
let i=0,p=0;
const $=s=>document.querySelector(s);
function draw(){
  const it=D[i]; p=Math.min(p,Math.max(it.imgs.length-1,0));
  $('#count').innerHTML='<b>'+(i+1)+'</b> / '+D.length;
  $('#shot').innerHTML = it.imgs.length
    ? '<img src="'+it.imgs[p]+'" alt="">'
    : '<div class="noimg">nessuna immagine</div>';
  $('#thumbs').innerHTML = it.imgs.length>1
    ? it.imgs.map((s,k)=>'<img src="'+s+'" class="'+(k===p?'on':'')+'" data-k="'+k+'">').join('')
    : '';
  $('#brand').textContent=it.brand; $('#name').textContent=it.name;
  $('#price').textContent=it.price.toLocaleString('it-IT',{maximumFractionDigits:0})+' \u20ac';
  $('#likes').textContent=it.likes+' \u2764';
  $('#up').textContent=it.when; $('#age').textContent=it.age;
  $('#rank').textContent='#'+(i+1)+' per like';
  $('#go').href=it.url;
}
function go(d){ i=(i+d+D.length)%D.length; p=0; draw(); }
document.addEventListener('keydown',e=>{
  if(e.key==='ArrowRight'||e.key===' ')go(1);
  if(e.key==='ArrowLeft')go(-1);
  if(e.key==='ArrowDown'){p=(p+1)%Math.max(D[i].imgs.length,1);draw();}
});
document.addEventListener('click',e=>{
  const t=e.target.closest('#thumbs img'); if(t){p=+t.dataset.k;draw();}
});
draw();
"""


def render_viewer(top, pool, lte_works) -> str:
    """One item per screen, arrow keys to move, thumbnails for extra photos."""
    data = []
    for r in top:
        created = r.get("created")
        data.append({
            "brand": r["brand"], "name": r["name"], "price": r["price"],
            "likes": r["likes"], "url": r["url"], "imgs": r.get("imgs") or [],
            "when": (datetime.fromtimestamp(created, timezone.utc).strftime("%d %b %Y")
                     if created else "-"),
            "age": (f"{(time.time()-created)/86400:.0f} giorni in vendita"
                    if created else ""),
        })
    note = ("annunci da 3-12 mesi e sotto i 3 mesi" if lte_works
            else "solo annunci recenti")
    return ("<!doctype html>\n<html lang=\"it\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>Vestiaire - Top {len(top)} per like</title>"
            f"<style>{VIEWER_CSS}</style></head><body><div class=\"wrap\">"
            "<div class=\"top\"><span class=\"counter\" id=\"count\"></span>"
            "<span id=\"rank\" style=\"color:var(--dim);font-size:13px\"></span>"
            f"<span class=\"title\">scarpe donna &gt; {FLOOR_CENTS//100} &euro; "
            f"ancora in vendita &middot; {pool:,} annunci esaminati &middot; {note}</span></div>"
            "<div class=\"stage\"><div class=\"shot\"><div id=\"shot\"></div>"
            "<div class=\"thumbs\" id=\"thumbs\"></div></div>"
            "<div class=\"side\"><div><div class=\"brand\" id=\"brand\"></div>"
            "<h1 id=\"name\"></h1></div>"
            "<div class=\"big\"><span class=\"price\" id=\"price\"></span>"
            "<span class=\"likes\" id=\"likes\"></span></div>"
            "<dl><dt>Caricato il</dt><dd id=\"up\"></dd>"
            "<dt>Da quanto</dt><dd id=\"age\"></dd></dl>"
            "<a class=\"go\" id=\"go\" target=\"_blank\" rel=\"noopener\">"
            "Apri su Vestiaire</a></div></div>"
            "<div class=\"bottom\"><button onclick=\"go(-1)\">&larr; Precedente</button>"
            "<button onclick=\"go(1)\">Successivo &rarr;</button>"
            "<span class=\"hint\">frecce &larr; &rarr; per scorrere, &darr; per le altre foto</span>"
            "</div></div><script>const D="
            # Escape inside the DATA only: a product name containing "</script>"
            # would otherwise close the block early. The document's own closing
            # tag is appended afterwards and must stay intact.
            + json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
            + ";" + VIEWER_JS + "</script></body></html>")


if __name__ == "__main__":
    sys.exit(main())
