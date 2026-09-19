"""Which catalog filters actually partition Vinted's results?

Pagination stops at ~960 listings per query, so total coverage is capped unless
the query itself can be sliced. On Vestiaire the slice was a createdAt window,
halved recursively until each piece fit. The equivalent here has to be found:
a parameter the page genuinely honours, not one it silently ignores.

A filter that "works" must do two things - change the result count, AND return
different ids. A parameter that is ignored gives back page 1 unchanged, which
looks like success if you only count rows.
"""
import re, sys, time, random
import requests
UA=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
s=requests.Session(); s.headers.update({"User-Agent":UA,"Accept-Language":"it-IT,it;q=0.9"})
s.get("https://www.vinted.it/",timeout=45)
BASE={"search_text":"prada shoes"}

def ids(params, label):
    time.sleep(random.uniform(2.5,4.5))
    r=s.get("https://www.vinted.it/catalog", params=params, timeout=60)
    i=set(re.findall(r"/items/(\d{6,12})-", r.text))
    pr=[float(x) for x in re.findall(r'\\?"amount\\?":\\?"([\d.]+)', r.text)][:96]
    rng=f"{min(pr):.0f}-{max(pr):.0f}" if pr else "-"
    print(f"  {label:<38} {r.status_code}  {len(i):>3} ids  price range {rng}")
    return i

base = ids(BASE, "baseline")
tests = [
    ({**BASE, "price_from": "0",   "price_to": "50"},    "price_from=0 price_to=50"),
    ({**BASE, "price_from": "200","price_to": "2000"},   "price_from=200 price_to=2000"),
    ({**BASE, "order": "newest_first"},                  "order=newest_first"),
    ({**BASE, "order": "price_high_to_low"},             "order=price_high_to_low"),
    ({**BASE, "status_ids[]": "6"},                      "status_ids[]=6 (new w/ tag)"),
    ({**BASE, "size_ids[]": "775"},                      "size_ids[]=775"),
    ({**BASE, "color_ids[]": "1"},                       "color_ids[]=1"),
]
for params, label in tests:
    got = ids(params, label)
    if got:
        ov = len(got & base) / len(got)
        print(f"     overlap with baseline: {ov*100:.0f}%  "
              f"{'-> IGNORED' if ov > 0.9 else '-> honoured'}")
