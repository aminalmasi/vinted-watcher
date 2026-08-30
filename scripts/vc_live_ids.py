"""Dump a handful of LIVE listing ids, so the apiv2 rate can be probed locally.

apiv2 answers the cluster for live items but 404s sold ones, and search (the
only source of ids) 403s the cluster. So the ids have to be fetched here and
the probe run there.
"""
import json, os, random, sys, time
import requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vestiaire.client import FIELDS, LOCALE, SEARCH, UA
from vestiaire.run import BRANDS, SHOES_WOMEN

S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9",
                  "Origin": "https://www.vestiairecollective.com",
                  "Referer": "https://www.vestiairecollective.com/",
                  "x-usecase": "catalog", "Content-Type": "application/json"})
ids = []
for bid in list(BRANDS)[:3]:
    time.sleep(random.uniform(6, 10))
    r = S.post(SEARCH, json={"pagination": {"offset": 0, "limit": 48},
                             "fields": FIELDS,
                             "filters": {"brand.id": [bid], "categoryLvl0.id": [SHOES_WOMEN],
                                         "sold": False},
                             "locale": LOCALE, "sort": "recency"}, timeout=45)
    if r.status_code == 200:
        ids += [str(i["id"]) for i in (r.json().get("items") or [])]
print("LIVE_IDS " + ",".join(ids[:90]))
