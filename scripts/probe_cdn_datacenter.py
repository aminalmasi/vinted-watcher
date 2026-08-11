"""Will the image CDN serve a datacenter IP (GitHub runner) with no proxy?

If yes, images can be fetched on Actions and the university IP never touches
Vinted at all — and it costs no metered proxy traffic either.
"""
import json, logging, os, sys, time
import requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vintedwatch.client import VintedClient  # noqa: E402

logging.basicConfig(level=logging.WARNING)
state = json.load(open("data/state.json"))
c = VintedClient(token_cache=state.get("token"))
items = c.search({"search_text": "prada shoes", "order": "newest_first", "per_page": 5}, page=1) or []
photos = (items[0].get("photos") or []) if items else []
if not photos:
    sys.exit("no photos in feed")
urls = [p["full_size_url"] for p in photos[:3]]

print(f"testing {len(urls)} image URLs from this runner, NO proxy\n")
plain = requests.Session()          # deliberately no proxies configured
ok = tot = 0
t0 = time.time()
for u in urls:
    try:
        r = plain.get(u, timeout=30)
        tot += len(r.content)
        ok += r.status_code == 200
        print(f"  HTTP {r.status_code}  {len(r.content)//1024:>4} KB  {u[:70]}")
    except Exception as e:
        print(f"  FAILED {type(e).__name__}: {e}")
print(f"\n  {ok}/{len(urls)} downloaded, {tot/1024:.0f} KB in {time.time()-t0:.1f}s, "
      f"ZERO metered proxy traffic")
