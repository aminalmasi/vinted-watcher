"""Embed the corpus with one named encoder.

Four families, three loading paths, because the local checkpoints are in
different formats:
  CLIP-L / CLIP-H   HF CLIPModel        -> get_image_features
  SigLIP2           HF SiglipModel      -> get_image_features
  SigLIP1           open_clip           -> the local copy is an open_clip
                                           snapshot with no HF config.json
  DINOv2            timm                -> the existing baseline

Everything is L2-normalised fp16 so all five indexes are directly comparable
and cosine similarity stays a plain matmul.
"""
from __future__ import annotations
import glob, json, os, sys, time
import numpy as np
import torch
from PIL import Image
from torch.utils.data import DataLoader, Dataset

DATA = os.path.expanduser("~/vestiaire_data")
HF = "/extra/malmasik/hf_models"
OUT = f"{DATA}/embeddings"
NAME = os.environ.get("EMB_NAME", "clipL")
BATCH = int(os.environ.get("EMB_BATCH", "128"))
# Shard across jobs. CLIP-H and SigLIP1 need 13-16 hours on a V100 for the full
# 734,847 photos and were killed by the 8-hour limit with nothing saved. Slicing
# the file list means each job finishes well inside the limit, and a loss costs
# one shard rather than everything.
SHARD_I = int(os.environ.get("EMB_SHARD_I", "0"))
SHARD_N = int(os.environ.get("EMB_SHARD_N", "1"))

SPECS = {
    "clipL":   ("hf_clip",   f"{HF}/OpenAI_CLIP/clip-vit-large-patch14"),
    "clipH":   ("hf_clip",   f"{HF}/OpenCLIP/CLIP-ViT-H-14-laion2B-s32B-b79K"),
    "siglip2": ("hf_siglip", f"{HF}/SigLIP2/google_siglip2_so400m_patch16_384"),
    "siglip1": ("open_clip", f"{HF}/SigLIP1/ViT-SO400M-14-SigLIP-384"),
    # Re-embedded on the CURRENT file set: the live harvest finished after the
    # first dinov2 run, so that index covers 655,747 photos while the others
    # cover 734,847. Comparing encoders across different indexes would make the
    # distractor set a hidden variable.
    "dinov2L2": ("timm", "vit_large_patch14_dinov2.lvd142m"),
}


class Photos(Dataset):
    def __init__(self, paths, tf):
        self.paths, self.tf = paths, tf
    def __len__(self):
        return len(self.paths)
    def __getitem__(self, i):
        try:
            with Image.open(os.path.join(DATA, self.paths[i])) as im:
                return self.tf(im.convert("RGB")), i
        except Exception:
            return self.tf(Image.new("RGB", (384, 384), (255, 255, 255))), i


def as_tensor(out):
    """transformers 5.x can return a ModelOutput from get_image_features rather
    than a bare tensor, which differs per architecture. Normalise it here so the
    caller never has to care which family it is holding."""
    if torch.is_tensor(out):
        return out
    for attr in ("image_embeds", "pooler_output", "last_hidden_state"):
        v = getattr(out, attr, None)
        if torch.is_tensor(v):
            return v if v.dim() == 2 else v.mean(dim=1)
    if isinstance(out, (tuple, list)) and torch.is_tensor(out[0]):
        return out[0]
    raise TypeError(f"cannot extract features from {type(out).__name__}")


def build(kind, path, dev):
    if kind == "hf_clip":
        from transformers import CLIPModel, CLIPImageProcessor
        import torchvision.transforms as T
        m = CLIPModel.from_pretrained(path).eval().to(dev).half()
        p = CLIPImageProcessor.from_pretrained(path)
        sz = p.crop_size["height"]
        tf = T.Compose([T.Resize(sz, interpolation=T.InterpolationMode.BICUBIC),
                        T.CenterCrop(sz), T.ToTensor(),
                        T.Normalize(p.image_mean, p.image_std)])
        return (lambda x: as_tensor(m.get_image_features(pixel_values=x))), tf
    if kind == "hf_siglip":
        from transformers import SiglipModel, SiglipImageProcessor
        import torchvision.transforms as T
        m = SiglipModel.from_pretrained(path).eval().to(dev).half()
        p = SiglipImageProcessor.from_pretrained(path)
        sz = p.size["height"]
        tf = T.Compose([T.Resize((sz, sz), interpolation=T.InterpolationMode.BICUBIC),
                        T.ToTensor(), T.Normalize(p.image_mean, p.image_std)])
        return (lambda x: as_tensor(m.get_image_features(pixel_values=x))), tf
    if kind == "timm":
        import timm
        m = timm.create_model(path, pretrained=True, num_classes=0,
                              img_size=224).eval().to(dev).half()
        cfg = timm.data.resolve_data_config({}, model=m)
        cfg["input_size"] = (3, 224, 224)
        return (lambda x: as_tensor(m(x))), timm.data.create_transform(**cfg, is_training=False)
    if kind == "open_clip":
        import open_clip
        m, _, tf = open_clip.create_model_and_transforms(
            "hf-hub:" + path if path.startswith("hf-hub") else "ViT-SO400M-14-SigLIP-384",
            pretrained=os.path.join(path, "open_clip_model.safetensors"))
        m = m.eval().to(dev).half()
        return (lambda x: as_tensor(m.encode_image(x))), tf
    raise ValueError(kind)


def main() -> int:
    kind, path = SPECS[NAME]
    dev = "cuda"
    fwd, tf = build(kind, path, dev)
    paths = sorted([os.path.relpath(p, DATA) for p in
                    glob.glob(f"{DATA}/images/*/*.jpg") + glob.glob(f"{DATA}/live/*/*.jpg")])
    json.dump(paths, open(f"{OUT}/{NAME}_paths.json", "w"))
    full_n = len(paths)
    if SHARD_N > 1:
        lo = full_n * SHARD_I // SHARD_N
        hi = full_n * (SHARD_I + 1) // SHARD_N
        offsets = list(range(lo, hi))
        paths = paths[lo:hi]
        print(f"{NAME}: shard {SHARD_I+1}/{SHARD_N} = photos {lo:,}..{hi:,}", flush=True)
    else:
        offsets = list(range(full_n))
        print(f"{NAME}: {full_n:,} photos", flush=True)

    dl = DataLoader(Photos(paths, tf), batch_size=BATCH, num_workers=8,
                    pin_memory=True, shuffle=False)
    embs = None
    t0, done = time.time(), 0
    with torch.no_grad():
        for x, idx in dl:
            f = fwd(x.to(dev, non_blocking=True).half())
            f = torch.nn.functional.normalize(f.float(), dim=-1).half().cpu().numpy()
            if embs is None:
                embs = np.zeros((len(paths), f.shape[1]), dtype=np.float16)
                print(f"  dim {f.shape[1]}", flush=True)
            embs[idx.numpy()] = f
            done += len(idx)
            if done % 50000 < BATCH:
                r = done / (time.time() - t0)
                print(f"  {done:,}/{len(paths):,}  {r:.0f}/s  eta {(len(paths)-done)/r/60:.0f}m",
                      flush=True)
    tag = NAME if SHARD_N == 1 else f"{NAME}.part{SHARD_I}"
    np.save(f"{OUT}/{tag}.npy", embs)
    print(f"wrote {OUT}/{tag}.npy {embs.shape} in {(time.time()-t0)/60:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
