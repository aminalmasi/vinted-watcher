"""Price a Vinted listing against Vestiaire: what sold, and what is on sale.

Tier A matching — brand + shoe subcategory + condition. No colour, no size, and
crucially no model name: half of Vinted's titles are "Sandals" and Vestiaire's
model field is often empty, so anything that REQUIRED a name would fail on the
listings you most want priced. The name is used only to ORDER candidates, never
to select them, so a missing or wrong name costs ranking quality and nothing else.

Price is deliberately excluded from the similarity score. Ranking candidates by
how close their price is to the Vinted one and then taking the median of the
best matches is circular — it would hand back roughly the Vinted price and look
authoritative doing it.

Two arms are reported because they answer different questions:
  SOLD  what items like this were listed at when they actually sold
  LIVE  what sellers are currently asking, including items that never sell

Caveat kept in the output: Vestiaire's price on a sold item is its listing
price. Offers are private, so the buyer may have paid less — treat the sold
median as an upper bound on realised value, not a receipt.
"""

from __future__ import annotations

import json, logging, os, random, re, statistics as st, sys, time
import unicodedata
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vestiaire.client import FIELDS, LOCALE, PAGE, SEARCH, UA   # noqa: E402

BRAND = os.environ.get("VC_BRAND_ID", "")
CAT = os.environ.get("VC_CAT_ID", "")
COND = os.environ.get("VC_CONDITION_ID", "")
TITLE = os.environ.get("VC_TITLE", "")
PRICE = float(os.environ.get("VC_PRICE") or 0)
K = int(os.environ.get("VC_K", "5"))
PAGES = int(os.environ.get("VC_PAGES", "4"))
WINDOW_DAYS = int(os.environ.get("VC_WINDOW_DAYS", "365"))
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "compare.json")

log = logging.getLogger("cmp")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9",
                  "Origin": "https://www.vestiairecollective.com",
                  "Referer": "https://www.vestiairecollective.com/",
                  "x-usecase": "catalog", "Content-Type": "application/json"})


def query(sold: bool, offset: int, use_condition: bool):
    f = {"brand.id": [BRAND], "sold": sold,
         "createdAt": {"gte": int(time.time() - WINDOW_DAYS * 86400)}}
    if CAT:
        f["categoryLvl1.id"] = [CAT]
    if COND and use_condition:
        f["condition.id"] = [COND]
    time.sleep(random.uniform(6, 10))
    r = S.post(SEARCH, json={"pagination": {"offset": offset, "limit": PAGE},
                             "fields": FIELDS, "filters": f, "locale": LOCALE,
                             "sort": "recency"}, timeout=45)
    if r.status_code != 200:
        log.warning("HTTP %d", r.status_code)
        return [], 0
    j = r.json()
    return (j.get("items") or []), int((j.get("paginationStats") or {}).get("totalHits") or 0)


def collect(sold: bool):
    """Sample one arm. Falls back to no condition filter if it matches nothing."""
    use_cond, note = bool(COND), ""
    items, total = query(sold, 0, use_cond)
    if use_cond and not items:
        log.info("  condition.id matched nothing — retrying without it")
        use_cond, note = False, "condition filter dropped (no matches)"
        items, total = query(sold, 0, use_cond)
    out = list(items)
    for p in range(1, PAGES):
        if len(out) >= total:
            break
        more, _ = query(sold, p * PAGE, use_cond)
        if not more:
            break
        out.extend(more)
    return out, total, note


# Vinted titles are written in whatever language the seller likes, usually
# English; Vestiaire's names are Italian. Without this map "High Heels" and
# "Decollete" share no tokens and EVERY candidate scores 0.00 — which silently
# turns the top-k into an arbitrary slice rather than the closest matches.
SYNONYM = {
    "heel": "decollete", "heels": "decollete", "pump": "decollete",
    "pumps": "decollete", "tacco": "decollete", "tacchi": "decollete",
    "sandal": "sandali", "sandals": "sandali", "sandalo": "sandali",
    "sneaker": "sneakers", "trainer": "sneakers", "trainers": "sneakers",
    "ginnastica": "sneakers", "boot": "stivali", "boots": "stivali",
    "ankle": "stivaletti", "bootie": "stivaletti", "booties": "stivaletti",
    "loafer": "mocassini", "loafers": "mocassini", "mocassino": "mocassini",
    "ballet": "ballerine", "flats": "ballerine", "ballerina": "ballerine",
    "clog": "zoccoli", "clogs": "zoccoli", "mule": "zoccoli", "mules": "zoccoli",
    "slingback": "slingback", "espadrille": "espadrillas",
    "leather": "pelle", "suede": "scamosciato", "canvas": "tela",
    "patent": "vernice", "velvet": "velluto", "rubber": "gomma",
    "satin": "raso", "denim": "jeans",
}
STOP = {"in", "con", "di", "e", "the", "and", "a", "shoes", "scarpe", "size",
        "vintage", "rare", "womens", "woman", "donna", "taglia"}


def fold(s: str) -> str:
    """Strip accents. Italian names are full of them and the tokeniser splits on
    non-ASCII, so "Decollete" would never match "Decollete" without this."""
    return "".join(c for c in unicodedata.normalize("NFKD", s or "")
                   if not unicodedata.combining(c))


def tokens(s):
    out = set()
    for t in re.split(r"[^a-zA-Z0-9]+", fold(s).lower()):
        t = SYNONYM.get(t, t)
        if len(t) > 2 and t not in STOP:
            out.add(t)
    return out


def summarise(items, label, total, note):
    rows = []
    want = tokens(TITLE)
    for it in items:
        cents = (it.get("price") or {}).get("cents")
        if not cents:
            continue
        name = it.get("name") or ""
        t = tokens(name)
        # Similarity is text-only. Price must never influence the ranking.
        sim = len(t & want) / len(t | want) if (t and want) else 0.0
        rows.append({"price": cents / 100, "name": name, "sim": round(sim, 3),
                     "likes": it.get("likes"),
                     "url": "https://www.vestiairecollective.com" + (it.get("link") or "")})
    if not rows:
        print(f"\n{label}: no comparable items found")
        return {"n": 0}

    prices = sorted(r["price"] for r in rows)
    q1, q3 = st.quantiles(prices, n=4)[0], st.quantiles(prices, n=4)[2]
    # Take everything down to the similarity level that first reaches K, rather
    # than a hard slice at K. Titles like "Decollete" leave dozens of candidates
    # tied at the same score, and cutting inside a tie group picks arbitrarily:
    # on one real query that produced a "top-5 median" of 1,365 EUR for LIVE and
    # 92 EUR for SOLD on the same shoe. Widening to the whole tie group makes
    # the number stable and honest about how many items back it.
    ranked = sorted(rows, key=lambda r: -r["sim"])
    cutoff = ranked[min(K, len(ranked)) - 1]["sim"] if ranked else 0
    topk = [r for r in ranked if r["sim"] >= cutoff]
    kmed = st.median([r["price"] for r in topk]) if topk else 0
    matched = bool(topk) and cutoff > 0

    print(f"\n{label}  (sampled {len(rows)} of {total}{'; ' + note if note else ''})")
    print(f"   median  {st.median(prices):>8,.0f} EUR      IQR {q1:,.0f} - {q3:,.0f}")
    if matched:
        tie = f" ({len(topk)} tied at sim>={cutoff:.2f})" if len(topk) > K else ""
        print(f"   closest by name: median {kmed:,.0f} EUR{tie}")
        for r in topk[:K]:
            print(f"      {r['price']:>7,.0f} EUR  sim={r['sim']:.2f}  {r['name'][:44]}")
    else:
        # Every candidate scored zero, so "top-k" would just be an arbitrary
        # slice. Reporting its median as a price estimate would be inventing a
        # number. Use the tier-A median above instead.
        print(f"   top-{K}: NOT MEANINGFUL — no name overlap with any candidate; "
              f"rely on the median above")
    return {"n": len(rows), "total": total, "median": st.median(prices),
            "q1": q1, "q3": q3, "topk_median": kmed if matched else None,
            "topk_meaningful": matched, "note": note,
            "topk": topk}


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(f"Vinted item: {TITLE!r}  {PRICE:,.0f} EUR")
    print(f"Tier A match: brand={BRAND} subcategory={CAT or '-'} condition={COND or '-'}")

    res = {}
    for sold, label in ((True, "VESTIAIRE SOLD"), (False, "VESTIAIRE LIVE")):
        items, total, note = collect(sold)
        res["sold" if sold else "live"] = summarise(items, label, total, note)

    s, l = res.get("sold", {}), res.get("live", {})
    if s.get("n") and PRICE:
        print(f"\nVinted asks {PRICE:,.0f} EUR; comparable Vestiaire items sold "
              f"around {s['median']:,.0f} EUR "
              f"({s['median']/PRICE:.1f}x) and are listed around "
              f"{l.get('median', 0):,.0f} EUR")
    print("\nSold prices are LISTING prices at time of sale — Vestiaire keeps "
          "accepted offers private, so treat them as an upper bound.")
    json.dump({"vinted": {"title": TITLE, "price": PRICE}, **res},
              open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
