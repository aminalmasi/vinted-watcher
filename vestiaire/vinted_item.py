"""Read one Vinted listing into the attributes needed to match it on Vestiaire.

Runs on the CLUSTER with no proxy. Two things make that possible:

  * Vinted's API needs an anonymous token, not an account — loading the
    homepage sets `access_token_web` and the catalog API then answers. The 401
    we get without it is a missing token, not an IP block.
  * The item DETAIL endpoint (/api/v2/items/{id}) is a dead 404 and always has
    been, so the attributes come from the item HTML page instead, which the
    university IP serves fine.

Nothing here is parsed from the page's translation blobs: an early attempt
matched `catalog_id` inside an i18n string and produced a plausible-looking
wrong answer. Every pattern below is anchored to markup or JSON that only the
item itself produces.
"""

from __future__ import annotations

import json
import re

import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Vinted's wording -> Vestiaire condition id. Their five map cleanly onto ours.
CONDITION = [
    (("nuovo con", "con cartellino"), 1, "Mai indossato, con etichetta"),
    (("nuovo senza", "senza cartellino"), 2, "Mai indossato"),
    (("ottim",), 3, "Ottimo stato"),
    (("buon",), 4, "Buono stato"),
    (("discret", "soddisfacent"), 5, "Corretto"),
]

# Vinted shoe subcategory -> Vestiaire categoryLvl1 id. Matched on the slug,
# which is stable, rather than on display text.
CATEGORY = [
    (("tacchi-alti", "tacco", "decollete", "heels", "pumps"), "510", "Scarpe con tacco"),
    (("sandal", "infradito"), "507", "Sandali"),
    (("ginnastica", "sneaker", "trainer"), "64", "Scarpe da ginnastica"),
    (("stivaletti", "tronchetti", "ankle"), "511", "Stivaletti"),
    (("stivali", "boot"), "62", "Stivali"),
    (("mocassini", "loafer"), "505", "Mocassini"),
    (("ballerine", "ballet"), "506", "Ballerine"),
    (("zoccoli", "ciabatt", "clog", "mule"), "508", "Zoccoli"),
    (("espadrillas", "espadrille"), "1051", "Espadrillas"),
    (("derby", "stringate", "brogue"), "509", "Scarpe derby"),
]


def _pick(table, text):
    t = (text or "").lower()
    for keys, *rest in table:
        if any(k in t for k in keys):
            return rest
    return [None, None]


def clean_url(url: str) -> str:
    """Tolerate how URLs actually arrive: wrapped in <>, quoted, with stray
    whitespace, or carrying tracking query junk. A bare InvalidSchema traceback
    is a hostile way to say "you pasted markdown"."""
    u = (url or "").strip().strip("<>").strip("\"'").strip()
    if not u.startswith(("http://", "https://")):
        raise ValueError(f"not a URL: {url!r}")
    if "vinted." not in u:
        raise ValueError(f"not a Vinted URL: {u[:80]!r}")
    return u.split("?")[0]


def parse(url: str, session: requests.Session | None = None) -> dict:
    """Everything we can learn about a Vinted listing, from its public page."""
    url = clean_url(url)
    s = session or requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
    if not s.cookies.get("access_token_web"):
        s.get("https://www.vinted.it/", timeout=40)

    r = s.get(url, timeout=45)
    if r.status_code != 200:
        raise RuntimeError(f"Vinted returned HTTP {r.status_code} — "
                           "the listing is probably sold or deleted")
    t = r.text
    out: dict = {"url": url}

    # JSON-LD first: it is plain JSON. The `brand_dto` block is backslash-
    # escaped inside a script payload, so it needs the tolerant pattern.
    m = re.search(r'"brand":\{"@type":"Brand","name":"([^"]{1,60})"', t)
    if m:
        out["brand"] = m.group(1)
    m = re.search(r'\\?"brand_dto\\?":\{\\?"id\\?":(\d+),\\?"title\\?":\\?"([^"\\]{1,60})', t)
    if m:
        out["brand_id"] = int(m.group(1))
        out.setdefault("brand", m.group(2))

    # JSON-LD: the only place the numeric price appears unambiguously.
    m = re.search(r'"offers":\{[^{}]*"priceCurrency":"([A-Z]{3})","price":([\d.]+)', t)
    if m:
        out["currency"], out["price"] = m.group(1), float(m.group(2))

    m = re.search(r'itemProp="status"><span[^>]*>([^<]{1,40})', t)
    if m:
        out["condition_text"] = m.group(1).strip()
    m = re.search(r'itemProp="size"><span[^>]*>([^<]{1,20})', t)
    if m:
        out["size"] = m.group(1).strip()

    m = re.search(r'"name"\s*:\s*"([^"]{3,120})"\s*,\s*"description"', t)
    out["title"] = m.group(1) if m else None

    # Breadcrumb link, e.g. /catalog/543-scarpe-con-tacchi-alti
    cats = re.findall(r'catalog/(\d+)-([a-z0-9-]{3,60})', t)
    for cid, slug in cats:
        vid, vname = _pick(CATEGORY, slug)
        if vid:
            out["vinted_catalog"] = f"{cid}-{slug}"
            out["category_id"], out["category"] = vid, vname
            break

    cid, cname = _pick(CONDITION, out.get("condition_text"))
    out["condition_id"], out["condition"] = cid, cname

    out["photos"] = list(dict.fromkeys(
        re.findall(r'https://images\d*\.vinted\.net/[^"\\\s]+/f800/[^"\\\s]+', t)))[:6]
    return out


if __name__ == "__main__":
    import sys
    print(json.dumps(parse(sys.argv[1]), indent=2, ensure_ascii=False)[:1400])
