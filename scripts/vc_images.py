"""Download one shard of the sold-listing photo corpus.

Rate: the CDN was measured at 25/25 successes with a 4 s gap and 13/25 with 2 s,
failing as a contiguous tail — a token bucket that refills slower than 2 s. So
each client waits 4-6 s. The throughput comes from SHARDING across jobs, not
from crowding one client, which is both safer and the only way to fit inside a
6-hour job.

A shard is (brand, condition). It reads that brand's metadata artifact, keeps
the records for its condition, and fetches one photo each.

Failures are logged rather than silently skipped, and a contiguous run of them
aborts the shard: pushing on through a throttle is how you turn a rate limit
into a block.
"""

from __future__ import annotations

import json, logging, os, random, sys, tarfile, time
import requests

BRAND = os.environ.get("VC_BRAND", "809")
COND = os.environ.get("VC_COND", "3")
WIDTH = os.environ.get("VC_WIDTH", "400")
GAP = (float(os.environ.get("VC_GAP_MIN", "4.0")),
       float(os.environ.get("VC_GAP_MAX", "6.0")))
OFFSET = int(os.environ.get("VC_OFFSET", "0"))
LIMIT = int(os.environ.get("VC_LIMIT", "0"))
SRC = f"vc_sold_{BRAND}.jsonl"
TAG = f"{BRAND}_{COND}_{OFFSET}"
OUTDIR = f"img_{TAG}"
TARBALL = f"images_{TAG}.tar"
IMG = "https://images.vestiairecollective.com/images/resized/w={w},q=75/produit/{p}"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HDR = {"User-Agent": UA, "Referer": "https://www.vestiairecollective.com/",
       "Accept": "image/avif,image/webp,*/*"}

log = logging.getLogger("img")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not os.path.exists(SRC):
        log.error("missing %s — the metadata artifact was not downloaded", SRC)
        return 1
    rows = []
    for line in open(SRC, encoding="utf-8"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if str(r.get("cond")) == COND and r.get("pic"):
            rows.append(r)
    # Index-based sharding on a stable sort. (brand, condition) alone gives
    # wildly uneven shards — Gucci's "Ottimo stato" is ~7,500 photos, which at
    # a polite pace exceeds the 6-hour job ceiling, while small brands finish in
    # minutes. Slicing by offset/limit makes every shard the same size and
    # therefore predictable.
    rows.sort(key=lambda r: str(r.get("id")))
    if LIMIT:
        rows = rows[OFFSET:OFFSET + LIMIT]
    log.info("shard brand=%s cond=%s offset=%d limit=%s: %d photos (~%.1f h)",
             BRAND, COND, OFFSET, LIMIT or "all", len(rows),
             len(rows) * sum(GAP) / 2 / 3600)
    os.makedirs(OUTDIR, exist_ok=True)

    ok = miss = 0
    streak = 0
    t0 = time.time()
    for i, r in enumerate(rows, 1):
        p = r["pic"]
        src = p if str(p).startswith("http") else IMG.format(w=WIDTH, p=p)
        dest = os.path.join(OUTDIR, f"{r['id']}.jpg")
        if os.path.exists(dest):
            ok += 1
            continue
        time.sleep(random.uniform(*GAP))
        try:
            resp = requests.get(src, headers=HDR, timeout=40)
        except requests.RequestException as exc:
            miss += 1; streak += 1
            log.warning("  %s: %s", r["id"], type(exc).__name__)
        else:
            if resp.status_code == 200 and resp.content:
                with open(dest, "wb") as fh:
                    fh.write(resp.content)
                ok += 1; streak = 0
            else:
                miss += 1; streak += 1
                log.warning("  %s: HTTP %d", r["id"], resp.status_code)
        if streak >= 8:
            # Eight in a row is the throttle signature, not bad luck. Stop and
            # keep what we have; the shard can be rerun and will skip existing.
            log.error("!! 8 consecutive failures — throttled, stopping shard")
            break
        if i % 100 == 0:
            log.info("  %d/%d  ok=%d miss=%d  %.2f h elapsed",
                     i, len(rows), ok, miss, (time.time() - t0) / 3600)

    with tarfile.open(TARBALL, "w") as tar:
        tar.add(OUTDIR, arcname=os.path.basename(OUTDIR))
    log.info("\n%d images, %d missing -> %s (%.1f MB)", ok, miss, TARBALL,
             os.path.getsize(TARBALL) / 1024 / 1024)
    return 0


if __name__ == "__main__":
    sys.exit(main())
