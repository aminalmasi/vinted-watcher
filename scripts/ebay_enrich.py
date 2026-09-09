"""Fill out the thin product groups with eBay listings.

Only 73 groups have fewer than 6 listings, and they are genuinely rare models
(Hermes Tonight, Dior Spectadior, Louboutin Pik Boat) rather than the parsing
fragments that made the number look like 545 before the groups were merged.

Verification is strict, and deliberately so. The Browse API's Brand aspect
filter FAILS SILENTLY: asking for Prada in the shoe category returned 2.76M
items, the whole category, because Prada is not a recognised Brand value there
and eBay ignores the filter rather than returning nothing. A big `total` is
therefore no evidence of anything. So every candidate must independently show
BOTH the brand and the model token in its own title before it is accepted, and
groups that stay thin stay thin — that is the correct outcome, not a failure.

eBay-sourced items are tagged `source: ebay` so they can be ablated. They are
training enrichment only and must not enter the gold evaluation set: rare
high-value models are exactly what gets counterfeited, and a convincing fake
looks like the product without being it.
"""

from __future__ import annotations

import base64, json, os, random, re, sys, time, unicodedata
import requests

DATA = os.path.expanduser("~/vestiaire_data")
OUT = f"{DATA}/ebay"
THIN = f"{DATA}/ground_truth/thin_groups.json"
CAT = os.environ.get("EBAY_CATEGORY", "3034")          # women's shoes
NPHOTOS = int(os.environ.get("EBAY_PHOTOS", "3"))
WANT = int(os.environ.get("EBAY_WANT", "6"))           # target listings per group
IMG_SIZE = os.environ.get("EBAY_IMG", "s-l800")
API_GAP, CDN_GAP = (1.0, 1.6), (0.8, 1.4)

cfg = {}
for line in open(os.path.expanduser("~/.config/ebay.env")):
    if "=" in line and not line.startswith("#"):
        k, v = line.strip().split("=", 1)
        cfg[k] = v
MKT = cfg.get("EBAY_MARKETPLACE", "EBAY_GB")


def fold(s):
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "")
                if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.lower())


def token():
    auth = base64.b64encode(
        f"{cfg['EBAY_CLIENT_ID']}:{cfg['EBAY_CLIENT_SECRET']}".encode()).decode()
    r = requests.post("https://api.ebay.com/identity/v1/oauth2/token",
                      headers={"Authorization": f"Basic {auth}",
                               "Content-Type": "application/x-www-form-urlencoded"},
                      data={"grant_type": "client_credentials",
                            "scope": "https://api.ebay.com/oauth/api_scope"},
                      timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def accepts(title, brand, model):
    """Both brand and model must be present in the listing's own title."""
    t = fold(title)
    bw = [w for w in fold(brand).split() if len(w) > 2]
    if not any(w in t for w in bw):
        return False
    mw = [w for w in model.split("-") if len(w) > 2]
    return all(w in t for w in mw) if mw else False


def main() -> int:
    groups = json.load(open(THIN))
    tok = token()
    H = {"Authorization": f"Bearer {tok}", "X-EBAY-C-MARKETPLACE-ID": MKT}
    os.makedirs(OUT, exist_ok=True)
    meta = open(f"{OUT}/ebay_items.jsonl", "a", encoding="utf-8")
    tot_items = tot_shots = 0
    t0 = time.time()

    for gi, g in enumerate(groups, 1):
        q = f"{g['brand']} {g['model'].replace('-', ' ')}"
        time.sleep(random.uniform(*API_GAP))
        try:
            r = requests.get("https://api.ebay.com/buy/browse/v1/item_summary/search",
                             headers=H, params={"q": q, "limit": 50,
                                                "category_ids": CAT}, timeout=30)
        except requests.RequestException as exc:
            print(f"  [{gi}/{len(groups)}] {q}: {type(exc).__name__}")
            continue
        if r.status_code != 200:
            print(f"  [{gi}/{len(groups)}] {q}: HTTP {r.status_code}")
            continue
        j = r.json()
        cands = j.get("itemSummaries") or []
        good = [it for it in cands if accepts(it.get("title", ""), g["brand"], g["model"])]
        take = good[:max(0, WANT - g["have"])]
        print(f"  [{gi}/{len(groups)}] {q[:44]:<46} total={j.get('total'):>8} "
              f"got={len(cands):>3} verified={len(good):>3} taking={len(take)}")

        gdir = f"{OUT}/g{g['group_id']}"
        for it in take:
            iid = it.get("itemId", "").split("|")[-2] if "|" in it.get("itemId", "") else it.get("itemId")
            urls = [(it.get("image") or {}).get("imageUrl")] + \
                   [a.get("imageUrl") for a in (it.get("additionalImages") or [])]
            urls = [u for u in urls if u][:NPHOTOS]
            if not urls:
                continue
            os.makedirs(gdir, exist_ok=True)
            saved = 0
            for k, u in enumerate(urls, 1):
                big = re.sub(r"s-l\d+", IMG_SIZE, u)
                time.sleep(random.uniform(*CDN_GAP))
                try:
                    ir = requests.get(big, timeout=30)
                except requests.RequestException:
                    continue
                if ir.status_code == 200 and ir.content:
                    open(f"{gdir}/{iid}_{k}.jpg", "wb").write(ir.content)
                    saved += 1
            if saved:
                meta.write(json.dumps({
                    "group_id": g["group_id"], "source": "ebay", "id": iid,
                    "brand": g["brand"], "model": g["model"], "sub": g["sub"],
                    "title": it.get("title"),
                    "price": (it.get("price") or {}).get("value"),
                    "currency": (it.get("price") or {}).get("currency"),
                    "condition": it.get("condition"),
                    "url": it.get("itemWebUrl"), "photos": saved,
                }, ensure_ascii=False) + "\n")
                meta.flush()
                tot_items += 1
                tot_shots += saved

    print(f"\n{tot_items} listings, {tot_shots} photos -> {OUT} "
          f"in {(time.time()-t0)/60:.0f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
