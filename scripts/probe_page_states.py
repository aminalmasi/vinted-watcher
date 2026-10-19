"""Save the pages of listings in known states, to diff them offline.

The steals tracker's 19 fast "sales" now serve a page with photos but no buy
block at all. Whether that means sold or removed can only be told against
ground truth: sales the sold-tracker confirmed (can_buy=false, validated on
30 known sales), its soft-404 "gone" pages, and live listings.
"""
import glob, gzip, json, os, sys, time
sys.path.insert(0, "scripts")
from vinted_steals import UA
import requests

st = json.load(open("data/vinted_track.json"))
steals = [json.loads(l) for f in glob.glob("data/steals/days/*.jsonl") for l in open(f)]
watch = json.load(open("data/steals/state.json"))["watch"]
now = time.time()
sold = sorted(st["sold"].items(), key=lambda kv: kv[1].get("sold_seen") or 0)
old_sold = [(k, v["slug"]) for k, v in sold if now - (v.get("sold_seen") or now) > 3 * 86400][-4:]
new_sold = [(k, v["slug"]) for k, v in sold][-4:]
gone = [(k, v.get("slug")) for k, v in st.get("archive", {}).items() if v.get("final") == "gone" and v.get("slug")][-3:]
live = [(k, r["url"].split("-", 1)[1]) for k, r in list(watch.items())[-3:]]
fast = [(str(r["id"]), r["url"].split("-", 1)[1]) for r in steals if r.get("outcome") == "sold"][:6]
groups = {"tracker_sold_3d": old_sold, "tracker_sold_today": new_sold, "tracker_gone": gone,
          "live": live, "steals_fast": fast}
s = requests.Session()
s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
s.get("https://www.vinted.it/", timeout=45)
os.makedirs("pages", exist_ok=True)
for g, items in groups.items():
    for iid, slug in items:
        time.sleep(5)
        r = s.get(f"https://www.vinted.it/items/{iid}-{slug}", timeout=60)
        with gzip.open(f"pages/{g}__{iid}.html.gz", "wt") as fh:
            fh.write(r.text)
        print(f"{g:<20} {iid} {r.status_code} {len(r.text)//1024} KB", flush=True)
