"""Does the query photo index matter? (_1 is the main shot, _2/_3 often backs.)

Queries were sampled uniformly over verified PHOTOS, so a back view or a sole
close-up can end up as the query. That is realistic but uncontrolled: if photo 2
and 3 retrieve far worse, the headline numbers are an average over a variable
nobody chose, and a fair comparison needs it either fixed or aggregated.
"""
import collections, glob, json, os
import numpy as np, torch
DATA=os.path.expanduser("~/vestiaire_data"); GT=f"{DATA}/ground_truth"
paths=json.load(open(f"{DATA}/embeddings/siglip2_paths.json"))
embs=np.load(f"{DATA}/embeddings/siglip2.npy")
base=[os.path.basename(p)[:-4] for p in paths]
lid=np.array([b.split("_")[0] for b in base])
pidx=np.array([int(b.split("_")[1]) if "_" in b else 0 for b in base])  # 0 = sold, 1 photo
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
allg={str(it["id"]) for g in gs.values() for it in g["items"]}
ingrp=np.array([x in allg for x in lid])
brand={}
for pat in (f"{DATA}/meta/*.jsonl", f"{DATA}/live_meta/*.jsonl"):
    for f in glob.glob(pat):
        for line in open(f,encoding="utf-8"):
            r=json.loads(line); brand[str(r["id"])]=str(r["brand"])
bmap={b:i for i,b in enumerate(sorted(set(brand.values())))}
bcode=np.array([bmap.get(brand.get(x,""),-1) for x in lid])
gcode=np.array([verified.get(x,-1) for x in lid]); jcode=np.array([junk.get(x,-1) for x in lid])
dev="cuda"
X=torch.from_numpy(embs).to(dev).half()
G=torch.from_numpy(gcode).to(dev); J=torch.from_numpy(jcode).to(dev)
IG=torch.from_numpy(ingrp).to(dev); B=torch.from_numpy(bcode).to(dev)
L={v:k for k,v in enumerate(sorted(set(lid)))}
Li=torch.tensor([L[x] for x in lid],device=dev)
qidx=np.where(gcode>=0)[0]
byg=collections.defaultdict(set)
for i in qidx: byg[int(gcode[i])].add(str(lid[i]))
valid=np.array([i for i in qidx if len(byg[int(gcode[i])]-{str(lid[i])})>0])
print("query photo index distribution:", dict(collections.Counter(pidx[valid])))
stats=collections.defaultdict(lambda:[0,0.0,0.0])
for s_ in range(0,len(valid),128):
    q=torch.tensor(valid[s_:s_+128],device=dev)
    sim=X[q].float()@X.T.float()
    sim[Li[q][:,None]==Li[None,:]]=-2
    sim[(J[None,:]==G[q][:,None])&(G[None,:]<0)]=-2
    sim[(B[q][:,None]==B[None,:])&~IG[None,:]]=-2
    top=sim.topk(10,dim=1).indices
    for r,qi in enumerate(q.tolist()):
        rel=(G[top[r]]==G[qi]).float()
        k=int(pidx[qi]); st=stats[k]
        st[0]+=1; st[1]+=rel[0].item(); st[2]+=rel.mean().item()
print(f"\n{'photo':>7}{'n':>7}{'P@1':>9}{'P@10':>9}")
for k in sorted(stats):
    n,p1,p10=stats[k]
    lbl={0:"sold(1)",1:"live _1",2:"live _2",3:"live _3"}.get(k,str(k))
    print(f"{lbl:>7}{int(n):>7}{p1/n:>9.3f}{p10/n:>9.3f}")
