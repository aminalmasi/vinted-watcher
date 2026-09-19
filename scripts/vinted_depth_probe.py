"""How deep does Vinted's catalog paginate, and do pages actually differ?

The tracker assumed 3 pages was a meaningful window; 93-98% of listings that
"disappeared" turned out to be alive, i.e. simply pushed past page 3. Sweeping
deeper only helps if the pages are real - a site that silently repeats page 1,
or caps at page N, would make a deep sweep pure waste.
"""
import re, sys, time, random
import requests
UA=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
s=requests.Session(); s.headers.update({"User-Agent":UA,"Accept-Language":"it-IT,it;q=0.9"})
s.get("https://www.vinted.it/",timeout=45)
seen={}
for page in (1,2,3,5,10,20,40):
    time.sleep(random.uniform(2,4))
    r=s.get("https://www.vinted.it/catalog",
            params={"search_text":"prada shoes","page":page},timeout=60)
    ids=set(re.findall(r"/items/(\d{6,12})-", r.text))
    overlap = len(ids & seen.get(1,set())) if page>1 else 0
    print(f"  page {page:>2}: HTTP {r.status_code}, {len(ids):>3} ids, "
          f"{len(r.text)/1024/1024:.1f} MB decompressed, overlap with p1 = {overlap}")
    seen[page]=ids
    if r.status_code!=200 or not ids: break
allids=set().union(*seen.values())
print(f"\n  union across probed pages: {len(allids)} distinct listings")
