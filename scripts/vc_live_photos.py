"""One shard of the LIVE corpus: photo paths from apiv2, then 3 photos each.

Both steps live in the same job because their rates differ by 10x and keeping
them together avoids a 240k-row intermediate artifact:

  apiv2   measured 25/25 ok at 0.5 s — used at ~1 s, it only supplies the photo
          list, which search does not return (search gives exactly one path).
  CDN     measured 25/25 at 4 s and 13/25 at 2 s — used at 4-6 s. This is what
          dominates: three photos at ~5 s each is ~16 s per listing.

Three photos rather than five: the live corpus turned out to be ~240k listings,
not the 86k the facet counts suggested (Elasticsearch term aggregations are
approximate and undercounted by 2.8x). Five photos would need 14.5 days at the
polite rate; three fits the budget, and for instance-level ground truth three
views — query plus two positives — is enough.
"""

from __future__ import annotations

import json, logging, os, random, sys, tarfile, time
import requests

BRAND = os.environ.get("VC_BRAND", "2")
OFFSET = int(os.environ.get("VC_OFFSET", "0"))
LIMIT = int(os.environ.get("VC_LIMIT", "1000"))
NPHOTOS = int(os.environ.get("VC_NPHOTOS", "3"))
WIDTH = os.environ.get("VC_WIDTH", "400")
API_GAP = (0.8, 1.4)
CDN_GAP = (4.0, 6.0)

SRC = f"vc_live_{BRAND}.jsonl"
TAG = f"{BRAND}_{OFFSET}"
OUTDIR, TARBALL = f"live_{TAG}", f"live_{TAG}.tar"
APIV2 = ("https://apiv2.vestiairecollective.com/products/{id}"
         "?isoCountry=IT&x-siteid=12&x-language=it&x-currency=EUR")
IMG = "https://images.vestiairecollective.com/images/resized/w={w},q=75/produit/{p}"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

log = logging.getLogger("live")
S = requests.Session(); S.headers.update({"User-Agent": UA})
IMGH = {"User-Agent": UA, "Referer": "https://www.vestiairecollective.com/",
        "Accept": "image/avif,image/webp,*/*"}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not os.path.exists(SRC):
        log.error("missing %s", SRC); return 1
    rows = [json.loads(l) for l in open(SRC, encoding="utf-8")]
    rows.sort(key=lambda r: str(r.get("id")))
    rows = rows[OFFSET:OFFSET + LIMIT]
    log.info("shard brand=%s offset=%d n=%d  (~%.1f h)", BRAND, OFFSET, len(rows),
             len(rows) * (1.1 + NPHOTOS * 5.0) / 3600)
    os.makedirs(OUTDIR, exist_ok=True)

    meta_out = open(f"{OUTDIR}/paths.jsonl", "w", encoding="utf-8")
    got = shots = gone = 0
    api_streak = cdn_streak = 0

    for i, r in enumerate(rows, 1):
        pid = r["id"]
        time.sleep(random.uniform(*API_GAP))
        try:
            resp = S.get(APIV2.format(id=pid), timeout=30)
        except requests.RequestException as exc:
            log.warning("  %s apiv2 %s", pid, type(exc).__name__)
            api_streak += 1
            if api_streak >= 10:
                log.error("!! 10 consecutive apiv2 failures — stopping"); break
            continue
        if resp.status_code != 200:
            # A live listing that sold since the metadata sweep. Expected, and
            # worth recording: it becomes a known sold/live pair later.
            gone += 1; api_streak += 1
            if api_streak >= 10:
                log.error("!! 10 consecutive apiv2 failures — stopping"); break
            continue
        api_streak = 0
        d = (resp.json() or {}).get("data") or {}
        pics = []
        for p in (d.get("pictures") or []):
            p = p.get("path") if isinstance(p, dict) else p
            if p:
                pics.append(p)
        meta_out.write(json.dumps({"id": pid, "pics": pics,
                                   "sold": d.get("sold"),
                                   "price": (d.get("price") or {}).get("cents"),
                                   "model": (d.get("model") or {}).get("name"),
                                   "color": (d.get("color") or {}).get("name"),
                                   "material": (d.get("material") or {}).get("name"),
                                   }, ensure_ascii=False) + "\n")
        for k, path in enumerate(pics[:NPHOTOS], 1):
            dest = f"{OUTDIR}/{pid}_{k}.jpg"
            if os.path.exists(dest):
                continue
            time.sleep(random.uniform(*CDN_GAP))
            src = path if str(path).startswith("http") else IMG.format(w=WIDTH, p=path)
            try:
                ir = requests.get(src, headers=IMGH, timeout=40)
            except requests.RequestException:
                cdn_streak += 1; continue
            if ir.status_code == 200 and ir.content:
                open(dest, "wb").write(ir.content)
                shots += 1; cdn_streak = 0
            else:
                cdn_streak += 1
            if cdn_streak >= 8:
                log.error("!! 8 consecutive CDN failures — throttled, stopping")
                break
        if cdn_streak >= 8:
            break
        got += 1
        if i % 100 == 0:
            log.info("  %d/%d listings, %d photos, %d gone", i, len(rows), shots, gone)

    meta_out.close()
    with tarfile.open(TARBALL, "w") as t:
        t.add(OUTDIR, arcname=OUTDIR)
    log.info("\n%d listings, %d photos, %d sold-since -> %s (%.1f MB)",
             got, shots, gone, TARBALL, os.path.getsize(TARBALL) / 1024 / 1024)
    return 0


if __name__ == "__main__":
    sys.exit(main())
