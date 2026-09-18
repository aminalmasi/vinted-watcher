"""Does the embedding encode the SOURCE rather than the shoe?

If a linear probe can separate eBay photos from Vestiaire photos using only the
embedding, then domain information is baked into the representation, and
cross-domain retrieval will suffer no matter how good the encoder is at shoes.
That would be the argument for background normalisation. If the probe is near
chance, background is not the bottleneck and whitening would be effort spent on
a non-problem.

A linear probe is the right instrument: it asks whether the information is
present and trivially accessible, not whether some deep model could dig it out.
"""
from __future__ import annotations
import glob, json, os, random, sys
import numpy as np
import torch
from PIL import Image
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score

DATA = os.path.expanduser("~/vestiaire_data")
TAG = "dinov2L"


def embed(paths, model, tf, dev):
    out = []
    for i in range(0, len(paths), 64):
        ims = []
        for p in paths[i:i+64]:
            try:
                with Image.open(p) as im:
                    ims.append(tf(im.convert("RGB")))
            except Exception:
                pass
        if not ims:
            continue
        with torch.no_grad():
            f = model(torch.stack(ims).to(dev).half())
            f = torch.nn.functional.normalize(f.float(), dim=-1)
        out.append(f.cpu().numpy())
    return np.vstack(out) if out else np.zeros((0, 1024))


def main() -> int:
    import timm
    ebay = sorted(glob.glob(f"{DATA}/ebay/*/*.jpg"))
    print(f"eBay photos: {len(ebay)}")
    if len(ebay) < 50:
        print("too few eBay photos"); return 1

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = timm.create_model("vit_large_patch14_dinov2.lvd142m", pretrained=True,
                              num_classes=0, img_size=224).eval().to(dev).half()
    cfg = timm.data.resolve_data_config({}, model=model); cfg["input_size"] = (3, 224, 224)
    tf = timm.data.create_transform(**cfg, is_training=False)
    E = embed(ebay, model, tf, dev)
    print(f"embedded {E.shape[0]} eBay photos", flush=True)

    # matched sample of Vestiaire photos
    paths = json.load(open(f"{DATA}/embeddings/{TAG}_paths.json"))
    V = np.load(f"{DATA}/embeddings/{TAG}.npy")
    random.seed(0)
    pick = random.sample(range(len(paths)), E.shape[0])
    Vs = V[pick].astype(np.float32)

    X = np.vstack([E, Vs]); y = np.r_[np.ones(len(E)), np.zeros(len(Vs))]
    auc = cross_val_score(LogisticRegression(max_iter=2000, C=1.0), X, y,
                          cv=5, scoring="roc_auc")
    acc = cross_val_score(LogisticRegression(max_iter=2000, C=1.0), X, y,
                          cv=5, scoring="accuracy")
    print(f"\nlinear probe, eBay vs Vestiaire ({len(E)} vs {len(Vs)}):")
    print(f"  ROC AUC  {auc.mean():.3f} +- {auc.std():.3f}")
    print(f"  accuracy {acc.mean():.3f} +- {acc.std():.3f}   (chance = 0.500)")
    print("\n  AUC ~0.5 -> source is not encoded; background is not the bottleneck")
    print("  AUC >0.9 -> domain is baked in; normalisation or augmentation matters")

    # how far apart are the two clouds, relative to within-domain spread?
    ce, cv = E.mean(0), Vs.mean(0)
    between = np.linalg.norm(ce - cv)
    within = (np.linalg.norm(E - ce, axis=1).mean() + np.linalg.norm(Vs - cv, axis=1).mean()) / 2
    print(f"\n  between-domain centroid distance : {between:.3f}")
    print(f"  mean within-domain spread        : {within:.3f}")
    print(f"  ratio                            : {between/within:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
