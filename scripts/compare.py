"""One command: Vinted URL in, Vestiaire price comparison out.

The two halves cannot share a machine — Vestiaire 403s the university IP and
Vinted blocks datacenter IPs — so this runs the Vinted half locally, dispatches
the Vestiaire half to GitHub Actions, waits, and prints the result.

    python scripts/compare.py https://www.vinted.it/items/123-something
"""

from __future__ import annotations

import json, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vestiaire.brands import resolve            # noqa: E402
from vestiaire.vinted_item import parse         # noqa: E402

GH = "/extra/malmasik/.local/bin/gh"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sh(*a, **kw):
    return subprocess.run(a, capture_output=True, text=True, cwd=REPO, **kw)


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    url = sys.argv[1]
    k = sys.argv[2] if len(sys.argv) > 2 else "5"

    print("reading the Vinted listing...")
    it = parse(url)
    bid, bname = resolve(it.get("brand"))
    print(f"  {it.get('brand')} -> Vestiaire brand {bid} ({bname})")
    print(f"  {it.get('condition_text')} -> {it.get('condition')} "
          f"(id {it.get('condition_id')})")
    print(f"  {it.get('vinted_catalog')} -> {it.get('category')} "
          f"(id {it.get('category_id')})")
    print(f"  size {it.get('size')} · asking {it.get('price')} {it.get('currency')}")
    if not bid:
        sys.exit("Could not map the brand to Vestiaire — stopping rather than "
                 "comparing against the wrong brand.")

    args = [GH, "workflow", "run", "vc-compare.yml",
            "-f", f"brand_id={bid}",
            "-f", f"cat_id={it.get('category_id') or ''}",
            "-f", f"condition_id={it.get('condition_id') or ''}",
            "-f", f"title={(it.get('title') or '')[:120]}",
            "-f", f"price={it.get('price') or 0}", "-f", f"k={k}"]
    r = sh(*args)
    if r.returncode:
        sys.exit(f"dispatch failed: {r.stderr.strip()[:300]}")
    print("\ndispatched; waiting for the Vestiaire query (~3 min)...")

    time.sleep(12)
    rid = None
    for _ in range(10):
        q = sh(GH, "run", "list", "--workflow=vc-compare.yml", "--limit", "1",
               "--json", "databaseId", "-q", ".[0].databaseId")
        rid = (q.stdout or "").strip()
        if rid:
            break
        time.sleep(6)
    if not rid:
        sys.exit("could not find the dispatched run")
    sh(GH, "run", "watch", rid, "--interval", "20")
    log = sh(GH, "run", "view", rid, "--log")
    body = log.stdout
    start = body.find("Vinted item:")
    print("\n" + "=" * 68)
    for line in body[start:].splitlines() if start >= 0 else []:
        line = line.split("\t")[-1]
        if line.startswith("##[") or "Post job" in line:
            break
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
