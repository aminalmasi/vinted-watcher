"""Exactly what a listing gives us with no extra requests (Option A)."""
import json, logging, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vintedwatch.client import VintedClient  # noqa: E402

logging.basicConfig(level=logging.WARNING)
state = json.load(open("data/state.json"))
c = VintedClient(token_cache=state.get("token"))
items = c.search({"search_text": "prada shoes", "order": "newest_first", "per_page": 5}, page=1) or []
it = items[0]
print("=== per-listing fields already in every sweep ===")
for k in ("id", "title", "brand_title", "size_title", "status", "url",
          "view_count", "favourite_count", "promoted"):
    print(f"  {k:17s} = {it.get(k)!r}")
print(f"  {'price':17s} = {it.get('price')}")
print(f"  {'total_item_price':17s} = {it.get('total_item_price')}")
print(f"  {'service_fee':17s} = {it.get('service_fee')}")
u = it.get("user") or {}
print(f"  {'seller':17s} = id={u.get('id')} login={u.get('login')!r} business={u.get('business')}")
ib = it.get("item_box") or {}
print(f"  {'item_box':17s} = {ib.get('first_line')!r} / {ib.get('second_line')!r}")
print(f"  {'accessibility':17s} = {str(ib.get('accessibility_label'))[:120]!r}")
ph = it.get("photos") or []
print(f"\n=== photos: {len(ph)} ===")
for i, p in enumerate(ph, 1):
    th = sorted(((t.get('type'), t.get('width'), t.get('height')) for t in (p.get('thumbnails') or [])),
                key=lambda x: x[1] or 0)
    print(f"  #{i}: {p.get('width')}x{p.get('height')}  main={p.get('is_main')}  colour={p.get('dominant_color')}")
    if i == 1:
        print(f"      thumbnail sizes: {th}")
        print(f"      full_size_url  : {str(p.get('full_size_url'))[:92]}")
print(f"\n[probe] {c.bytes_uncompressed/1024:.0f} KB")
