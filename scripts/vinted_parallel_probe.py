#!/usr/bin/env python3
"""Does a second Actions job double our catalog throughput, or share a wall?

Two jobs are only worth building if the limit is genuinely per-IP. The
evidence so far is contradictory - one cycle did ~1,470 requests clean while
another was refused after ~350 - so this measures it rather than assuming.

Design: identical workloads, one variable.

    control (SHARDS=1)   one job walks N catalog pages
    test    (SHARDS=2)   two jobs walk N/2 each, at the same time

If the limit is per-IP, both test shards finish their half cleanly and the
pair moves 2x what one job moves. If it is shared - by subnet, or applied to
the endpoint rather than the client - the shards will block at a COMBINED
count near the control's, and blocking will start at roughly the same
wall-clock moment in both. Each shard therefore reports its public IP and a
timestamp for every block, which is what distinguishes the two outcomes.

Deliberately bounded: it stops at the first sustained refusal rather than
hunting for the exact ceiling, and N is small enough to answer the question
without being a load test.
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import time

import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
SHARD = int(os.environ.get("SHARD", "0"))
SHARDS = int(os.environ.get("SHARDS", "1"))
N = int(os.environ.get("N", "240"))          # total across all shards
GAP = float(os.environ.get("GAP", "2.5"))
GIVE_UP = 6                                   # consecutive refusals = blocked

BRANDS = ["Gucci", "Chanel", "Hermes", "Christian Louboutin", "Dior", "Prada",
          "Saint Laurent", "Valentino", "Golden Goose", "Bottega Veneta"]


def my_ip(s) -> str:
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            r = s.get(url, timeout=20)
            if r.status_code == 200:
                return r.text.strip()
        except requests.RequestException:
            pass
    return "?"


def main() -> int:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
    ip = my_ip(s)
    mine = N // SHARDS
    t0 = time.time()
    print(f"shard {SHARD}/{SHARDS}  ip={ip}  budget={mine} requests  gap={GAP}s",
          flush=True)

    try:
        s.get("https://www.vinted.it/", timeout=45)
    except requests.RequestException:
        pass

    # Interleave work so the shards cover different brands rather than racing
    # on the same query - that is how the real job would be split.
    plan = []
    for page in range(1, 25):
        for bi, b in enumerate(BRANDS):
            if bi % SHARDS == SHARD:
                plan.append((b, page))
    plan = plan[:mine]

    ok = blocked = 0
    streak = 0
    first_block_at = None
    codes: dict[int, int] = {}

    for i, (brand, page) in enumerate(plan, 1):
        time.sleep(random.uniform(GAP, GAP * 1.3))
        try:
            r = s.get("https://www.vinted.it/catalog",
                      params={"search_text": f"{brand} shoes", "page": page},
                      timeout=45)
            c = r.status_code
        except requests.RequestException:
            c = 0
        codes[c] = codes.get(c, 0) + 1

        if c == 200:
            ok += 1
            streak = 0
        else:
            blocked += 1
            streak += 1
            if first_block_at is None:
                first_block_at = i
                print(f"  FIRST REFUSAL at request {i} "
                      f"(t+{time.time()-t0:.0f}s, HTTP {c})", flush=True)
            if streak >= GIVE_UP:
                print(f"  {streak} refusals in a row - stopping at request {i}",
                      flush=True)
                break
        if i % 40 == 0:
            print(f"  {i:>4}/{mine}  ok={ok}  refused={blocked}  "
                  f"t+{time.time()-t0:.0f}s", flush=True)

    el = time.time() - t0
    result = {"shard": SHARD, "shards": SHARDS, "ip": ip, "ok": ok,
              "refused": blocked, "first_block_at": first_block_at,
              "elapsed_s": round(el), "codes": codes}
    print(f"\nRESULT {json.dumps(result)}", flush=True)
    print(f"shard {SHARD}: {ok} successful catalog pages in {el/60:.1f} min",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
