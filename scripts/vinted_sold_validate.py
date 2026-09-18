"""Does can_buy actually mean SOLD, or just "not purchasable right now"?

The previous watcher's false positives came from treating a disappearance as a
sale when it could equally be a hidden listing, a deleted one, or a seller on
holiday. The fields it used for confirmation (is_closed, item_closing_action)
are gone from the current HTML, so can_buy and JSON-LD availability are the
replacements - and they carry the same risk of conflating "sold" with
"temporarily unbuyable".

Two populations with known labels:
  SOLD  the August archive - listings the old pipeline flagged as sold and
        which were still reachable enough to archive photos from
  LIVE  ids scraped from a catalog page minutes ago, so they are on sale now

If can_buy separates them cleanly the signal is usable. If sold listings show a
mix, it does not mean sold and the tracker needs a different confirmation.
"""
from __future__ import annotations
import collections, glob, json, os, random, re, sys, time
import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
N = int(os.environ.get("N_EACH", "30"))


def state(s, url):
    try:
        r = s.get(url, timeout=45)
    except requests.RequestException as e:
        return f"ERR {type(e).__name__}", None
    if r.status_code != 200:
        return f"HTTP {r.status_code}", None
    t = r.text
    can = re.search(r'\\?"can_buy\\?":\s*(true|false)', t)
    av = re.search(r'"availability":"([^"]+)"', t)
    res = re.search(r'\\?"is_reserved\\?":\s*(true|false)', t)
    hid = re.search(r'\\?"is_hidden\\?":\s*(true|false)', t)
    return (f"can_buy={can.group(1) if can else '-'}",
            {"availability": av.group(1) if av else "-",
             "reserved": res.group(1) if res else "-",
             "hidden": hid.group(1) if hid else "-"})


def main() -> int:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
    s.get("https://www.vinted.it/", timeout=45)

    sold = []
    for f in glob.glob(os.path.expanduser("~/vinted_archive/*/meta.json")):
        try:
            u = json.load(open(f)).get("url")
            if u:
                sold.append(u)
        except Exception:
            pass
    random.seed(1)
    sold = random.sample(sold, min(N, len(sold)))

    cat = s.get("https://www.vinted.it/catalog",
                params={"search_text": "prada shoes"}, timeout=60).text
    live = [f"https://www.vinted.it/items/{i}-{sl}"
            for i, sl in re.findall(r"/items/(\d{6,12})-([a-z0-9-]{3,60})", cat)]
    live = random.sample(live, min(N, len(live)))
    print(f"checking {len(sold)} archived-SOLD and {len(live)} currently-LIVE listings\n",
          flush=True)

    for label, urls in (("SOLD (archived Aug)", sold), ("LIVE (catalog now)", live)):
        c = collections.Counter()
        extra = collections.Counter()
        for u in urls:
            time.sleep(random.uniform(1.2, 2.0))
            st, det = state(s, u)
            c[st] += 1
            if det:
                extra[f"availability={det['availability']}"] += 1
                if det["reserved"] == "true":
                    extra["reserved=true"] += 1
                if det["hidden"] == "true":
                    extra["hidden=true"] += 1
        print(f"{label}: n={len(urls)}")
        for k, v in c.most_common():
            print(f"   {k:<24} {v:>3}  {100*v/len(urls):5.1f}%")
        for k, v in extra.most_common(5):
            print(f"      {k:<26} {v}")
        print(flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
