"""Embed the whole photo corpus for retrieval experiments.

DINOv2 is the default because the task is instance/product-level retrieval
rather than semantic similarity — we need "this exact shoe", not "a black
heeled shoe", and DINOv2 is the strongest off-the-shelf model for that.

Input size is 224, not the checkpoint's native 518. The source photos are
400x400 (a limit of how they were fetched, not of the CDN), so 518 would be
pure upsampling: ~5x the patches and ~4x the runtime for information that is
not in the file. timm interpolates the position embeddings for us.

Embeddings are L2-normalised fp16, so cosine similarity is a plain matmul and
655k x 1024 fits in about 1.3 GB — no ANN index needed at this scale.

Written in chunks so a preempted job resumes instead of restarting.
"""

from __future__ import annotations

import glob, json, os, sys, time
import numpy as np
import torch
from PIL import Image
from torch.utils.data import DataLoader, Dataset

DATA = os.path.expanduser("~/vestiaire_data")
OUT = f"{DATA}/embeddings"
MODEL = os.environ.get("EMB_MODEL", "vit_large_patch14_dinov2.lvd142m")
TAG = os.environ.get("EMB_TAG", "dinov2L")
SIZE = int(os.environ.get("EMB_SIZE", "224"))
BATCH = int(os.environ.get("EMB_BATCH", "256"))
CHUNK = 50_000


class Photos(Dataset):
    def __init__(self, paths, tf):
        self.paths, self.tf = paths, tf

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, i):
        p = self.paths[i]
        try:
            with Image.open(os.path.join(DATA, p)) as im:
                return self.tf(im.convert("RGB")), i
        except Exception:
            return torch.zeros(3, SIZE, SIZE), i


def main() -> int:
    import timm
    paths = sorted([os.path.relpath(p, DATA) for p in
                    glob.glob(f"{DATA}/images/*/*.jpg") + glob.glob(f"{DATA}/live/*/*.jpg")])
    print(f"{len(paths):,} photos", flush=True)
    os.makedirs(OUT, exist_ok=True)
    json.dump(paths, open(f"{OUT}/{TAG}_paths.json", "w"))

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = timm.create_model(MODEL, pretrained=True, num_classes=0, img_size=SIZE)
    model = model.eval().to(dev).half()
    cfg = timm.data.resolve_data_config({}, model=model)
    cfg["input_size"] = (3, SIZE, SIZE)
    tf = timm.data.create_transform(**cfg, is_training=False)
    print(f"{MODEL} on {dev}, input {SIZE}, dim {model.num_features}", flush=True)

    dl = DataLoader(Photos(paths, tf), batch_size=BATCH, num_workers=8,
                    pin_memory=True, shuffle=False)
    embs = np.zeros((len(paths), model.num_features), dtype=np.float16)
    t0, done = time.time(), 0
    with torch.no_grad():
        for x, idx in dl:
            f = model(x.to(dev, non_blocking=True).half())
            f = torch.nn.functional.normalize(f.float(), dim=-1).half()
            embs[idx.numpy()] = f.cpu().numpy()
            done += len(idx)
            if done % CHUNK < BATCH:
                r = done / (time.time() - t0)
                print(f"  {done:,}/{len(paths):,}  {r:.0f} img/s  "
                      f"eta {(len(paths)-done)/r/60:.0f} min", flush=True)
                np.save(f"{OUT}/{TAG}.npy", embs)
    np.save(f"{OUT}/{TAG}.npy", embs)
    print(f"\nwrote {OUT}/{TAG}.npy  {embs.shape}  "
          f"{os.path.getsize(f'{OUT}/{TAG}.npy')/1024**3:.2f} GB "
          f"in {(time.time()-t0)/60:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
