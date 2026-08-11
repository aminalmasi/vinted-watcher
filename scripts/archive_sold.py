#!/usr/bin/env python3
"""Archive sold listings: one folder per listing, images + metadata.

Runs ON THE CLUSTER, deliberately without the proxy:
  * item pages  — the university IP can load them; our proxy exits are 403.
  * images      — the CDN serves anyone, so these cost nothing and risk nothing.

Written to be a good citizen, because the alternative is losing access:
  * strictly sequential, jittered 4-9 s between page loads (~1 req / 6 s),
  * stops dead on the first 403/429 rather than pushing through,
  * gives up after consecutive failures instead of hammering,
  * resumes by skipping folders that already exist, so a stop costs nothing,
  * a hard --limit so a run can never become unbounded.

Output lives OUTSIDE the git repo: the repo is public and this is gigabytes.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time

import html as htmlmod

import requests

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(REPO, "data", "state.json")
OUT = "/extra/malmasik/vinted_archive"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
PAGE_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}
IMG_URL = re.compile(r'https://images\d*\.vinted\.net/[^"\\\s]+')
STOP_STATUSES = (403, 429)


def unescape(u: str) -> str:
    return u.replace("\\u0026", "&").replace("\\/", "/")


def meta_tag(html: str, name: str) -> str | None:
    """Read a <meta> tag's content — plain HTML, not the escaped JSON blobs."""
    m = re.search(rf'<meta\s+(?:name|property)="{re.escape(name)}"\s+content="([^"]*)"', html)
    return htmlmod.unescape(m.group(1)) if m else None


def description_of(html: str, title: str | None) -> str | None:
    """The seller's text.

    It is only exposed through the SEO meta tags, formatted as
    "<title> - <description>", so the title prefix is stripped back off.
    """
    d = meta_tag(html, "description") or meta_tag(html, "og:description")
    if not d:
        return None
    d = d.strip()
    if title:
        pref = f"{title.strip()} - "
        if d.startswith(pref):
            d = d[len(pref):]
    return d.strip() or None


def safe(s: str, n: int = 40) -> str:
    s = re.sub(r"[^\w\-]+", "-", (s or "").strip().lower()).strip("-")
    return (s[:n] or "x")


def folder_name(rec: dict, brand: str | None) -> str:
    b = safe(brand or (rec.get("search") or "").replace(" shoes", ""), 24)
    # Prefer the buyer-facing total; sold records captured before that field was
    # stored only have the seller's asking price, so fall back to it.
    price = rec.get("total_price") or rec.get("price")
    try:
        price = f"{float(price):.2f}"
    except (TypeError, ValueError):
        price = "na"
    return f"{b}_{price}_{rec['id']}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--limit", type=int, default=25, help="max listings this run")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    state = json.load(open(STATE))
    now = time.time()
    sold = [dict(v, id=k) for k, v in state["sold"].items()
            if v.get("reported_at", 0) > now - args.days * 86400]
    sold.sort(key=lambda r: -r.get("reported_at", 0))
    os.makedirs(args.out, exist_ok=True)

    # Listings the seller deleted after the sale can never be archived. Remember
    # them, or every later run pays the same 6 s to rediscover the same 404.
    gone_path = os.path.join(args.out, "_gone.json")
    try:
        gone_ids = set(json.load(open(gone_path)))
    except (OSError, ValueError):
        gone_ids = set()

    have = {d.rsplit("_", 1)[-1] for d in os.listdir(args.out)}
    todo = [r for r in sold if r["id"] not in have and r["id"] not in gone_ids]
    print(f"{len(sold)} sold in {args.days}d | {len(sold)-len(todo)} settled "
          f"({len(gone_ids & {r['id'] for r in sold})} deleted by seller) | "
          f"{len(todo)} to go | doing up to {args.limit} now")
    if args.dry_run:
        for r in todo[:args.limit]:
            print("  would fetch", r["url"])
        return 0

    s = requests.Session()
    s.headers.update(PAGE_HEADERS)
    done = failed = gone = 0
    consecutive = 0

    for rec in todo[:args.limit]:
        if consecutive >= 3:
            print("!! 3 failures in a row — stopping rather than pushing on")
            break
        url = rec.get("url")
        try:
            r = s.get(url, timeout=45, allow_redirects=True)
        except requests.RequestException as exc:
            print(f"  {rec['id']}: {type(exc).__name__}")
            failed += 1; consecutive += 1
            time.sleep(random.uniform(8, 15))
            continue

        if r.status_code in STOP_STATUSES:
            print(f"!! HTTP {r.status_code} on {rec['id']} — Vinted is asking us to stop. "
                  f"Halting immediately.")
            break
        if r.status_code in (404, 410):
            # Seller deleted it after the sale; nothing to archive, ever.
            gone += 1; consecutive = 0
            gone_ids.add(rec["id"])
            with open(gone_path, "w") as fh:
                json.dump(sorted(gone_ids), fh)
            print(f"  {rec['id']}: gone (404)")
            time.sleep(random.uniform(4, 9))
            continue
        if r.status_code != 200:
            print(f"  {rec['id']}: HTTP {r.status_code}")
            failed += 1; consecutive += 1
            time.sleep(random.uniform(8, 15))
            continue

        html = r.text
        urls, seen = [], set()
        for u in IMG_URL.findall(html):
            u = unescape(u)
            if "/f800/" in u and u not in seen:
                seen.add(u); urls.append(u)
        brand = None
        bm = re.search(r'\\"brand\\":\\"([^"\\]{1,40})\\"', html)
        if bm:
            brand = bm.group(1)
        desc = description_of(html, rec.get("title"))

        name = folder_name(rec, brand)
        d = os.path.join(args.out, name)
        os.makedirs(d, exist_ok=True)

        saved = 0
        for i, u in enumerate(urls, 1):
            ext = ".webp" if ".webp" in u.split("?")[0] else ".jpg"
            path = os.path.join(d, f"photo_{i}{ext}")
            try:
                ir = s.get(u, timeout=45, headers={"User-Agent": UA,
                                                   "Referer": "https://www.vinted.it/"})
                if ir.status_code == 200 and ir.content:
                    with open(path, "wb") as fh:
                        fh.write(ir.content)
                    saved += 1
            except requests.RequestException:
                pass
            time.sleep(random.uniform(0.3, 1.0))

        meta = {
            "id": int(rec["id"]),
            "title": rec.get("title"),
            "brand": brand or (rec.get("search") or "").replace(" shoes", "").title(),
            "search": rec.get("search"),
            "price": rec.get("price"),
            "currency": rec.get("currency"),
            "total_item_price": rec.get("total_price"),
            "url": url,
            "sold_detected_at": rec.get("reported_at"),
            "sold_detected_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                               time.gmtime(rec.get("reported_at", 0))),
            "hours_listed": rec.get("hours_listed"),
            "hours_exact": rec.get("hours_exact"),
            "given_delta": rec.get("given_delta"),
            "description": desc,
            "photo_count": saved,
            "photo_urls": urls,
        }
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
        if desc:
            with open(os.path.join(d, "description.txt"), "w", encoding="utf-8") as fh:
                fh.write(desc + "\n")

        done += 1; consecutive = 0
        print(f"  {name}: {saved} photos{' + description' if desc else ''}")
        time.sleep(random.uniform(4, 9))

    print(f"\narchived {done}, deleted-before-archive {gone}, failed {failed} "
          f"-> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
