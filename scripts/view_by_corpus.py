"""View mix of SOLD photos vs LIVE photos.

Sold listings carry exactly one photo each, so whatever view that photo happens
to be IS the query for every sold item - there is no second angle to fall back
on. If sold photos skew to a different view than live ones, then the sold half
of the corpus is systematically easier or harder, and the earlier finding that
sold queries score 0.437 against live _1's 0.283 may be a view effect rather
than the background effect I assumed.
"""
import collections, json, os
import numpy as np
DATA=os.path.expanduser("~/vestiaire_data"); GT=f"{DATA}/ground_truth"
paths=json.load(open(f"{DATA}/embeddings/siglip2_paths.json"))
comp=np.load(f"{GT}/axis_composition_tags.npy")
view=np.load(f"{GT}/axis_viewpoint_tags.npy")
cn=json.load(open(f"{GT}/axis_composition_names.json"))
vn=json.load(open(f"{GT}/axis_viewpoint_names.json"))
base=[os.path.basename(p) for p in paths]
is_sold=np.array([p.startswith("images/") for p in paths])
pidx=np.array([int(b[:-4].split("_")[1]) if "_" in b[:-4] else 0 for b in base])

def show(mask, label, names, tags):
    n=mask.sum()
    c=collections.Counter(tags[mask].tolist())
    print(f"\n{label}  ({n:,} photos)")
    for k,v in c.most_common():
        print(f"   {names[k]:<9} {v:>8,}  {100*v/n:5.1f}%")

print("="*52); print("COMPOSITION"); print("="*52)
show(is_sold, "SOLD (1 photo each)", cn, comp)
show(~is_sold & (pidx==1), "LIVE photo _1", cn, comp)
show(~is_sold & (pidx==3), "LIVE photo _3", cn, comp)
print("\n"+"="*52); print("VIEWPOINT"); print("="*52)
show(is_sold, "SOLD (1 photo each)", vn, view)
show(~is_sold & (pidx==1), "LIVE photo _1", vn, view)
show(~is_sold & (pidx==3), "LIVE photo _3", vn, view)
