"""Merge sharded embedding parts into one array, refusing to guess.

Each shard covered a contiguous slice of the same sorted path list, so the
parts concatenate in shard order. The total is checked against the path file:
a silent size mismatch would misalign every embedding with its photo, which is
the kind of error that produces plausible-looking nonsense rather than a crash.
"""
from __future__ import annotations
import glob, json, os, sys
import numpy as np

OUT = os.path.expanduser("~/vestiaire_data/embeddings")
name = os.environ.get("EMB_NAME", "siglip1")
parts = sorted(glob.glob(f"{OUT}/{name}.part*.npy"),
               key=lambda p: int(p.split(".part")[1].split(".")[0]))
if not parts:
    sys.exit(f"no parts for {name}")
paths = json.load(open(f"{OUT}/{name}_paths.json"))
arrs = [np.load(p) for p in parts]
tot = sum(a.shape[0] for a in arrs)
print(f"{name}: {len(parts)} parts, {tot:,} rows, expected {len(paths):,}")
for p, a in zip(parts, arrs):
    print(f"   {os.path.basename(p):<26} {a.shape}")
if tot != len(paths):
    sys.exit(f"MISMATCH: {tot} rows vs {len(paths)} paths - refusing to merge")
full = np.concatenate(arrs, axis=0)
np.save(f"{OUT}/{name}.npy", full)
print(f"wrote {OUT}/{name}.npy {full.shape} "
      f"({os.path.getsize(f'{OUT}/{name}.npy')/1024**3:.2f} GB)")
