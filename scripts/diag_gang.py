"""Where do the 'wrong' results actually come from?

The suspicion: they are not wrong. 226,512 of 331,226 listings (68%) carry no
model token at all - a Dior Gang pump whose title is just "Decollete in Pelle"
sits in the unlabelled pool and is scored as a distractor even though it IS the
query's product. If most wrong results are unlabelled rather than belonging to
some other named group, then the benchmark is measuring incomplete annotation
rather than model error, and every number so far is a LOWER BOUND.
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
in_any_group={str(it["id"]) for g in gs.values() for it in g["items"]}
gcode=np.array([verified.get(x,-1) for x in lid]); jcode=np.array([junk.get(x,-1) for x in lid])
dev="cuda"
X=torch.from_numpy(embs).to(dev).half(); G=torch.from_numpy(gcode).to(dev)
J=torch.from_numpy(jcode).to(dev)
L={v:k for k,v in enumerate(sorted(set(lid)))}
Li=torch.tensor([L[x] for x in lid],device=dev)
qidx=np.where(gcode>=0)[0]
byg=collections.defaultdict(set)
for i in qidx: byg[int(gcode[i])].add(str(lid[i]))
valid=[i for i in qidx if len(byg[int(gcode[i])]-{str(lid[i])})>0]
random.seed(5); Q=random.sample(valid,150)
cnt=collections.Counter()
for qi in Q:
    sim=(X[torch.tensor([qi],device=dev)].float()@X.T.float())[0]
    sim[Li==Li[qi]]=-2; sim[(J==G[qi])&(G<0)]=-2
    for r in sim.topk(10).indices.tolist():
        if gcode[r]==gcode[qi]: cnt["correct (verified same group)"]+=1
        elif str(lid[r]) not in in_any_group: cnt["UNLABELLED (no model token at all)"]+=1
        else: cnt["in a different named group"]+=1
tot=sum(cnt.values())
print(f"\n150 queries x top-10 = {tot} results\n")
for k,v in cnt.most_common(): print(f"  {k:<40} {v:>5}  {100*v/tot:5.1f}%")
print(f"\ncorpus: {len(in_any_group):,} listings in a group, "
      f"{331226-len(in_any_group):,} unlabelled ({100*(331226-len(in_any_group))/331226:.0f}%)")
