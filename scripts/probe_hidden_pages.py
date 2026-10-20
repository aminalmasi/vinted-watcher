"""What distinguishes a live listing that no search shows any more?

93% of listings missing from two sweeps in a row were still live on their
page: brand unchanged, not reserved, can_buy true, is_hidden false. Save
their pages beside ordinary live ones, to diff offline.
"""
import gzip, json, os, sys, time
sys.path.insert(0, "scripts")
from vinted_steals import UA
import requests

st = json.load(open("data/steals/state.json"))
last = st["cycles"][-1]["at"]
w = st["watch"].values()
hidden = [r for r in w if r.get("checks", 0) > 0 and r.get("misses", 0) >= 2][:8]
normal = sorted((r for r in w if r.get("misses", 0) == 0 and r["last_live"] >= last - 600),
                key=lambda r: -r["first_seen"])[:4]
s = requests.Session()
s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
s.get("https://www.vinted.it/", timeout=45)
os.makedirs("pages", exist_ok=True)
for tag, rs in (("hidden", hidden), ("normal", normal)):
    for r in rs:
        time.sleep(5)
        p = s.get("https://www.vinted.it" + r["url"], timeout=60)
        with gzip.open(f"pages/{tag}__{r['id']}.html.gz", "wt") as fh:
            fh.write(p.text)
        print(tag, r["id"], p.status_code, len(p.text) // 1024, "KB", flush=True)
