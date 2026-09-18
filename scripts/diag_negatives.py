"""What ARE the wrong retrievals, actually?

Three hypotheses, and they need different fixes:
  1. photos of the query's OWN listing leaking in (a masking bug)
  2. same brand+model but a different SUBCATEGORY - genuinely the same product,
     split across two groups because grouping is (brand, model, subcategory).
     Hermes Oran appears as both "Sandali Oran" and "Ciabatte Oran"; if those
     are separate groups, retrieving one for the other is scored WRONG despite
     being right. That would be a ground-truth defect, not a model failure.
  3. genuinely different products
"""
import collections, json, os, random
import numpy as np, torch
DATA=os.path.expanduser("~/vestiaire_data"); GT=f"{DATA}/ground_truth"
paths=json.load(open(f"{DATA}/embeddings/siglip2_paths.json"))
embs=np.load(f"{DATA}/embeddings/siglip2.npy")
lid=np.array([os.path.basename(p)[:-4].split("_")[0] for p in paths])
gs={g["group_id"]:g for g in (json.loads(l) for l in open(f"{GT}/groups.jsonl",encoding="utf-8"))}
lab=json.load(open(f"{GT}/labels_all.json"))
ann={g["gid"]:g for g in lab if not g["skipped"]}
rej={str(x) for g in lab if not g["skipped"] for x in g["bad"]}
shown=json.load(open(f"{GT}/shown_ids.json"))
verified,junk={},{}
for k,ids in shown.items():
    gid=int(k)
    if gid not in ann: continue
    seen=set(ids)
    for s in seen:
        if s not in rej: verified[s]=gid
    for it in gs[gid]["items"]:
        s=str(it["id"])
        if s not in seen: junk[s]=gid
gcode=np.array([verified.get(x,-1) for x in lid]); jcode=np.array([junk.get(x,-1) for x in lid])
bm={}
for g in gs.values():
    for it in g["items"]: bm[str(it["id"])]=(g["brand_id"],g["model"])
key=[bm.get(x) for x in lid]
dev="cuda"
X=torch.from_numpy(embs).to(dev).half(); G=torch.from_numpy(gcode).to(dev)
J=torch.from_numpy(jcode).to(dev)
L={v:k for k,v in enumerate(sorted(set(lid)))}
Li=torch.tensor([L[x] for x in lid],device=dev)
qidx=np.where(gcode>=0)[0]
byg=collections.defaultdict(set)
for i in qidx: byg[int(gcode[i])].add(str(lid[i]))
valid=[i for i in qidx if len(byg[int(gcode[i])]-{str(lid[i])})>0]
random.seed(5); Q=random.sample(valid,120)
cnt=collections.Counter()
for qi in Q:
    sim=(X[torch.tensor([qi],device=dev)].float()@X.T.float())[0]
    sim[Li==Li[qi]]=-2
    sim[(J==G[qi])&(G<0)]=-2
    for r in sim.topk(10).indices.tolist():
        if lid[r]==lid[qi]: cnt["OWN LISTING (masking bug)"]+=1
        elif gcode[r]==gcode[qi]: cnt["correct"]+=1
        elif jcode[r]==gcode[qi]: cnt["junk leaked"]+=1
        elif key[r] is not None and key[r]==key[qi]:
            cnt["same brand+model, OTHER subcategory"]+=1
        else: cnt["genuinely different product"]+=1
tot=sum(cnt.values())
print(f"\n120 queries x top-10 = {tot} results\n")
for k,v in cnt.most_common(): print(f"  {k:<42} {v:>5}  {100*v/tot:5.1f}%")
# how much of the corpus is affected by the subcategory split at all?
sib=collections.defaultdict(set)
for g in gs.values(): sib[(g["brand_id"],g["model"])].add(g["subcategory"])
multi={k:v for k,v in sib.items() if len(v)>1}
print(f"\n(brand,model) pairs split across >1 subcategory: {len(multi)} of {len(sib)}")
for k,v in list(multi.items())[:6]: print(f"    {k[1]:<20} {sorted(v)}")
