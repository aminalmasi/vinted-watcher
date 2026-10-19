"""Are the steals tracker's fast "sales" sales?

The first hour marked 19 of 22 absent new listings sold within 30 minutes,
nearly all priced far below market (a Gucci bag at EUR 1, a Dior bag at 5).
That fits an instant purchase - and also Vinted pulling a likely counterfeit.
The check calls a listing sold when can_buy is false and it is not reserved;
this re-reads those pages and prints every flag in the buy block, plus any
status marker in the page, so the two can be told apart.
"""
import glob, json, re, sys, time
sys.path.insert(0, "scripts")
from vinted_steals import UA
import requests

recs = [json.loads(l) for f in glob.glob("data/steals/days/*.jsonl") for l in open(f)]
sold = [r for r in recs if r.get("outcome") == "sold"]
s = requests.Session()
s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
s.get("https://www.vinted.it/", timeout=45)
for r in sold:
    time.sleep(5)
    p = s.get("https://www.vinted.it" + r["url"], timeout=60)
    t = p.text.replace('\\"', '"')
    block = re.search(r'\{"can_buy":[^{}]{0,400}\}', t)
    marks = sorted(set(re.findall(r'data-testid="(item-status[^"]*|[^"]*sold[^"]*|[^"]*closed[^"]*|[^"]*hidden[^"]*)"', p.text)))
    avail = re.findall(r'"availability":"([^"]+)"', t)[:1]
    keys = sorted(set(re.findall(r'"(is_[a-z_]+)":(true)', t)))
    print(f"{r['id']} {p.status_code} {len(p.text)//1024}KB price {r['price']} | "
          f"{block.group(0)[:260] if block else 'no buy block'} | avail {avail} | testids {marks[:6]} | true flags {keys[:10]}",
          flush=True)
