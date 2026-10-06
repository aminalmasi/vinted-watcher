#!/usr/bin/env python3
"""Why do ~660 checks a cycle come back "unknown"?

An unknown means the item page was fetched with HTTP 200 but the can_buy flag
did not parse, so the listing is recorded as neither live, sold nor deleted.
It then stays tracked, stays absent, and accumulates at the head of the check
queue - 858 listings have now been missing for over a week, crowding out fresh
absences where the sales actually are.

Three explanations fit the symptom and they need different fixes, so this
looks at the page instead of guessing:

  markup change   can_buy renamed or moved, and the regex is simply stale
  soft block      a challenge or consent page served with HTTP 200
  partial fetch   the page is truncated before the flag appears

So: fetch the longest-absent listings, report status, size, which markers are
present, and what the page actually is. Nothing is changed here - this is only
a measurement, because the fix differs completely per cause.
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import time

import requests

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(REPO, "data", "vinted_track.json")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
N = int(os.environ.get("N", "12"))

# What the tracker looks for, plus things that would explain its absence.
MARKERS = {
    "can_buy": r'\\?"can_buy\\?":\s*(true|false)',
    "is_reserved": r'\\?"is_reserved\\?":\s*(true|false)',
    "availability": r'"availability":"([^"]+)"',
    "upload_date": r'\\?"code\\?":\\?"upload_date\\?"',
    "item_id": r'\\?"item_id\\?":\\?"?(\d+)',
    "is_closed": r'\\?"is_closed\\?":\s*(true|false)',
    "is_hidden": r'\\?"is_hidden\\?":\s*(true|false)',
    "item_closing": r'\\?"(item_closing_action|closed_at)\\?"',
    "price": r'\\?"amount\\?":\\?"([\d.]+)',
}
BLOCKS = {
    "cloudflare": r"(cf-browser-verification|Just a moment|cf_chl|__cf_bm)",
    "captcha": r"(captcha|CAPTCHA|hcaptcha|recaptcha)",
    "denied": r"(Access denied|Forbidden|blocked)",
    "consent": r"(consent|cookie-consent|gdpr)",
    "login": r"(Accedi|Log in|sign_in)",
}


def main() -> int:
    st = json.load(open(STATE))
    t = st["tracked"]
    now = time.time()
    # Control group matters: "deleted" is terminal, so classifying the shell
    # page as deleted would stop us tracking any LIVE listing that happened to
    # serve it. Compare the longest-absent against ones seen in the feed within
    # the hour - if the shell appears only among the absent, the inference is
    # safe; if it appears for fresh ones too, it is transient and must not be
    # treated as terminal.
    absent = sorted(t.items(), key=lambda kv: kv[1].get("last_seen", now))[:N * 3]
    fresh = [kv for kv in t.items()
             if now - kv[1].get("last_seen", 0) < 3600]
    random.shuffle(absent); random.shuffle(fresh)
    cand = [("ABSENT", k, v) for k, v in absent[:N // 2]] + \
           [("FRESH ", k, v) for k, v in fresh[:N // 2]]
    print(f"{len(cand)} pages: {N//2} long-absent, {min(N//2, len(fresh))} "
          f"seen within the hour\n", flush=True)

    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9",
                      "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
    s.get("https://www.vinted.it/", timeout=45)

    summary = {"total": 0, "has_can_buy": 0, "blocked": 0, "tiny": 0}
    for group, iid, rec in cand:
        time.sleep(random.uniform(2.5, 3.5))
        url = f"https://www.vinted.it/items/{iid}-{rec.get('slug','')}"
        try:
            r = s.get(url, timeout=45)
        except requests.RequestException as exc:
            print(f"\n{iid}: {type(exc).__name__}", flush=True)
            continue
        t_ = r.text
        kb = len(t_) // 1024
        gone_d = (now - rec.get("last_seen", now)) / 86400
        summary["total"] += 1

        found = [k for k, pat in MARKERS.items() if re.search(pat, t_)]
        blocks = [k for k, pat in BLOCKS.items() if re.search(pat, t_)]
        if "can_buy" in found:
            summary["has_can_buy"] += 1
        if blocks:
            summary["blocked"] += 1
        if kb < 100:
            summary["tiny"] += 1

        title = re.search(r"<title>(.{0,90})", t_)
        print(f"\n[{group}] {iid}  HTTP {r.status_code}  {kb} KB  "
              f"gone {gone_d:.1f}d", flush=True)
        print(f"  title   : {title.group(1).strip() if title else '?'}", flush=True)
        print(f"  markers : {found or 'NONE'}", flush=True)
        # the BLOCK markers fired on every page including good item pages -
        # those strings are in Vinted's standard bundle, so they are noise
        has_item = bool(re.search(r'\\?"item_id\\?"', t_))
        print(f"  shell?  : {'SHELL (no item data)' if not has_item else 'real item page'}",
              flush=True)
        # if can_buy is missing, show what the item blob does contain
        if "can_buy" not in found:
            m = re.search(r'\\?"item_id\\?"', t_)
            if m:
                seg = re.sub(r"\s+", " ", t_[max(0, m.start()-200):m.start()+700])
                print(f"  around item_id: ...{seg[:520]}...", flush=True)

    print(f"\n=== {summary['total']} pages: {summary['has_can_buy']} had can_buy, "
          f"{summary['blocked']} looked like a block, {summary['tiny']} under 100KB",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
