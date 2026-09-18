"""Is a margin of 0.01 small, or is that just SigLIP's scale?

I have been calling these margins tiny without establishing what range SigLIP
image-text cosines actually occupy. If every prompt scores within [0.05, 0.08],
a 0.01 gap is a third of the whole spread and is decisive. Normalising the
top-two margin by the spread across prompts answers it; so does a softmax,
which is what a classifier would actually use.
"""
import json, os
import numpy as np, torch
DATA=os.path.expanduser("~/vestiaire_data"); GT=f"{DATA}/ground_truth"
HF="/extra/malmasik/hf_models/SigLIP2/google_siglip2_so400m_patch16_384"
from transformers import SiglipModel, SiglipProcessor
dev="cuda"
model=SiglipModel.from_pretrained(HF).eval().to(dev)
proc=SiglipProcessor.from_pretrained(HF)
names=json.load(open(f"{GT}/axis_viewpoint_names.json"))
PR={"side":"a shoe photographed from the side, showing its profile",
"front":"a shoe photographed from the front, showing the toe",
"back":"a shoe photographed from behind, showing the heel counter",
"top":"a shoe photographed from directly above, looking down into it",
"sole":"a shoe turned over, showing the sole and tread underneath",
"angled":"a shoe photographed at a three-quarter angle"}
with torch.no_grad():
    tin=proc(text=[PR[k] for k in names], padding="max_length", return_tensors="pt").to(dev)
    T=model.get_text_features(**tin)
    if not torch.is_tensor(T): T=getattr(T,"text_embeds",None) or getattr(T,"pooler_output")
    T=torch.nn.functional.normalize(T.float(),dim=-1)
X=np.load(f"{DATA}/embeddings/siglip2.npy")[:40000].astype(np.float32)
S=(torch.from_numpy(X).to(dev)@T.T).cpu().numpy()
top2=np.sort(S,axis=1)[:,::-1][:,:2]
spread=S.max(1)-S.min(1)
marg=top2[:,0]-top2[:,1]
print(f"across {len(S):,} photos x {len(names)} viewpoint prompts:")
print(f"  cosine values      : min {S.min():.4f} max {S.max():.4f} mean {S.mean():.4f}")
print(f"  spread per photo   : median {np.median(spread):.4f}")
print(f"  top-2 margin       : median {np.median(marg):.4f}")
print(f"  margin / spread    : median {np.median(marg/np.maximum(spread,1e-6)):.3f}"
      f"   <- fraction of the whole range")
sm=torch.softmax(torch.from_numpy(S)*100.0,dim=1).numpy()   # siglip logit scale ~100
print(f"  softmax(top) @scale100: median {np.median(sm.max(1)):.3f}")
