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
IMG = "https://images.vestiairecollective.com/images/resized/w=400,q=70/produit/{p}"

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
    probe_all = search("2", 0, now - 365*86400, limit=1)
    probe_old = search("2", 0, now - 365*86400, lte=now - 90*86400, limit=1)
    def hits(j):
        return (j or {}).get("paginationStats", {}).get("totalHits")
    lte_works = bool(probe_old and hits(probe_old) and hits(probe_old) != hits(probe_all))
    log.info("createdAt lte supported: %s  (all=%s, older-than-90d=%s)",
             lte_works, hits(probe_all), hits(probe_old))

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
                    rows.append({
                        "id": str(it.get("id")), "name": it.get("name") or "",
                        "brand": (it.get("brand") or {}).get("name") or name,
                        "price": (it.get("price") or {}).get("cents", 0) / 100,
                        "likes": it["likes"], "created": it.get("createdAt"),
                        "pic": (pics[0] or {}).get("path") if pics else None,
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

    for r in top:
        r["img"] = None
        if not r["pic"]:
            continue
        try:
            ir = S.get(IMG.format(p=r["pic"]), timeout=45,
                       headers={"Referer": "https://www.vestiairecollective.com/",
                                "Accept": "image/avif,image/webp,*/*"})
            if ir.status_code == 200 and ir.content:
                mime = ir.headers.get("content-type", "image/jpeg").split(";")[0]
                r["img"] = f"data:{mime};base64," + base64.b64encode(ir.content).decode()
        except requests.RequestException:
            pass
        time.sleep(random.uniform(0.5, 1.2))

    open(OUT, "w", encoding="utf-8").write(render(top, len(uniq), lte_works))
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


if __name__ == "__main__":
    sys.exit(main())
