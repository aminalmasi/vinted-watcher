"""Apply the ILIAS-trained linear probe on top of SigLIP1 features.

The thesis probe is a free 1152x1152 linear map (adapter_mode=free_linear,
local InfoNCE, tau 0.05, 8 hard negatives) learned over SigLIP1 SO400M
features. Applying it is a matmul, so the interesting part is not cost but
whether the assumption holds: the probe was trained on precomputed features,
and we feed it L2-NORMALISED ones. If that differs from training, the map is
being applied in the wrong space and results will be worse rather than
subtly off - which the comparison against raw SigLIP1 will show plainly.
"""
from __future__ import annotations
import os, sys
import numpy as np
import torch

OUT = os.path.expanduser("~/vestiaire_data/embeddings")
REL = os.path.expanduser("~/instance-based-composed-image-retrieval-release-v0.1.0")
CKPT = os.environ.get("PROBE", "infonce")
SRC = os.environ.get("SRC", "siglip1")
DST = f"{SRC}_probe_{CKPT}"

sd = torch.load(f"{REL}/siglip1_ilias_alltrain_{CKPT}.pt", map_location="cpu",
                weights_only=False)["model_state_dict"]
W = sd["proj.weight"].float()
b = sd["proj.bias"].float()
X = np.load(f"{OUT}/{SRC}.npy")
print(f"{SRC} {X.shape} -> probe {tuple(W.shape)}")
assert X.shape[1] == W.shape[1], f"dim mismatch {X.shape[1]} vs {W.shape[1]}"

dev = "cuda" if torch.cuda.is_available() else "cpu"
W, b = W.to(dev), b.to(dev)
out = np.zeros((X.shape[0], W.shape[0]), dtype=np.float16)
B = 100_000
for i in range(0, X.shape[0], B):
    v = torch.from_numpy(X[i:i+B]).to(dev).float()
    y = torch.nn.functional.normalize(v @ W.T + b, dim=-1)
    out[i:i+B] = y.half().cpu().numpy()
np.save(f"{OUT}/{DST}.npy", out)
import shutil; shutil.copy(f"{OUT}/{SRC}_paths.json", f"{OUT}/{DST}_paths.json")
print(f"wrote {OUT}/{DST}.npy {out.shape}")
