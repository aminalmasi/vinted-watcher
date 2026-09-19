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
    if pairs:
        iid, slug = pairs[0]
        r2 = s.get(f"https://www.vinted.it/items/{iid}-{slug}", timeout=60)
        print(f"\n  item {iid} -> HTTP {r2.status_code}, {len(r2.text)//1024} KB")
        show("ITEM page", r2.text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
