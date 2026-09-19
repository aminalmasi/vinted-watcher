"""Is a listing's POSTING date available anywhere we already fetch?

The tracker records first_seen - when we discovered a listing - which is not
the same thing and is useless for "older than 6 months": the whole corpus was
seeded within days of each other. Pruning by age needs the seller's upload
date, so this checks the two places we already pay for: the catalog page and
the item page.
"""
from __future__ import annotations
import json, re, sys, time
import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
PATTERNS = [
    ("created_at_ts", r'\\?"created_at_ts\\?":\\?"?([0-9T:\-\.\+Z]{10,32})'),
    ("createdAt", r'\\?"createdAt\\?":\\?"?([0-9T:\-\.\+Z]{10,32})'),
    ("created_at", r'\\?"created_at\\?":\\?"?([0-9T:\-\.\+Z]{10,32})'),
    ("uploadedAt", r'\\?"uploaded_at\\?":\\?"?([0-9T:\-\.\+Z]{10,32})'),
    ("datePublished", r'"datePublished":"([^"]+)"'),
    ("relative-it", r'(\d+\s+(?:minut|or[ae]|giorn|settiman|mes|ann)\w*\s+fa)'),
]


def show(tag, text):
    print(f"  {tag}:", flush=True)
    hit = False
    for name, pat in PATTERNS:
        m = re.findall(pat, text)
        if m:
            hit = True
            uniq = list(dict.fromkeys(m))[:3]
            print(f"    {name:<16} {len(m):>4} hits  e.g. {uniq}", flush=True)
    if not hit:
        print("    no date field found", flush=True)


def main() -> int:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
    s.get("https://www.vinted.it/", timeout=45)

    r = s.get("https://www.vinted.it/catalog",
              params={"search_text": "gucci shoes"}, timeout=60)
    show("CATALOG page", r.text)
    pairs = re.findall(r"/items/(\d{6,12})-([a-z0-9-]{3,60})", r.text)
    time.sleep(3)
    for iid, slug in pairs[:3]:
        time.sleep(3)
        r2 = s.get(f"https://www.vinted.it/items/{iid}-{slug}", timeout=60)
        print(f"\n  item {iid} -> HTTP {r2.status_code}", flush=True)
        # Context matters more than the match: pruning on the wrong string
        # would discard listings that are not old at all.
        for m in re.finditer(PATTERNS[-1][1], r2.text):
            a, b = max(0, m.start()-110), min(len(r2.text), m.end()+40)
            ctx = re.sub(r"\s+", " ", r2.text[a:b])
            print(f"    >>> {m.group(1)!r}", flush=True)
            print(f"        ...{ctx}...", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
