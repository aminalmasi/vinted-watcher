"""Two independent axes instead of one muddled label set.

The first version put "pair" in the same softmax as "side" and "top", but a
pair photographed from the side is BOTH - the options were not mutually
exclusive, so similarity mass split across overlapping prompts and the margin
between the top two collapsed to ~0.005-0.010. That looked like the model
failing to see viewpoint; it was the label set being wrong.

Split into two softmaxes over genuinely exclusive options:
  composition  how many shoes and how they are presented
  viewpoint    which side of the shoe faces the camera

If the diagnosis is right, margins within each axis should widen sharply. If
they stay flat, the problem really is the encoder and hand-labelling is next.
"""
from __future__ import annotations
import collections, json, os, sys
import numpy as np
import torch

DATA = os.path.expanduser("~/vestiaire_data")
GT = f"{DATA}/ground_truth"
HF = "/extra/malmasik/hf_models/SigLIP2/google_siglip2_so400m_patch16_384"

AXES = {
    "composition": {
        "single":  "a product photo of one single shoe on its own",
        "pair":    "a product photo of two shoes, a matching pair together",
        "worn":    "a photo of a person wearing shoes on their feet",
        "boxed":   "a photo of a shoe box or its packaging",
        "closeup": "an extreme close-up of part of a shoe, showing a logo or buckle",
    },
    "viewpoint": {
        "side":    "a shoe photographed from the side, showing its profile",
        "front":   "a shoe photographed from the front, showing the toe",
        "back":    "a shoe photographed from behind, showing the heel counter",
        "top":     "a shoe photographed from directly above, looking down into it",
        "sole":    "a shoe turned over, showing the sole and tread underneath",
        "angled":  "a shoe photographed at a three-quarter angle",
    },
}


def main() -> int:
    from transformers import SiglipModel, SiglipProcessor
    dev = "cuda"
    model = SiglipModel.from_pretrained(HF).eval().to(dev)
    proc = SiglipProcessor.from_pretrained(HF)
    X = np.load(f"{DATA}/embeddings/siglip2.npy")
    print(f"{X.shape[0]:,} photos", flush=True)

    out = {}
    for axis, prompts in AXES.items():
        names = list(prompts)
        with torch.no_grad():
            tin = proc(text=[prompts[k] for k in names], padding="max_length",
                       return_tensors="pt").to(dev)
            T = model.get_text_features(**tin)
            if not torch.is_tensor(T):
                T = getattr(T, "text_embeds", None) or getattr(T, "pooler_output")
            T = torch.nn.functional.normalize(T.float(), dim=-1)
        tags = np.zeros(X.shape[0], dtype=np.int8)
        marg = np.zeros(X.shape[0], dtype=np.float16)
        B = 200_000
        for i in range(0, X.shape[0], B):
            V = torch.from_numpy(X[i:i+B]).to(dev).float()
            S = V @ T.T
            t2 = S.topk(2, dim=1)
            tags[i:i+B] = t2.indices[:, 0].cpu().numpy().astype(np.int8)
            marg[i:i+B] = (t2.values[:, 0] - t2.values[:, 1]).cpu().numpy()
        np.save(f"{GT}/axis_{axis}_tags.npy", tags)
        np.save(f"{GT}/axis_{axis}_margin.npy", marg)
        json.dump(names, open(f"{GT}/axis_{axis}_names.json", "w"))
        out[axis] = (names, tags, marg)
        c = collections.Counter(tags.tolist())
        print(f"\n{axis}  (median margin overall {np.median(marg):.4f})")
        for k, v in c.most_common():
            print(f"   {names[k]:<9} {v:>8,}  {100*v/len(tags):5.1f}%   "
                  f"median margin {np.median(marg[tags==k]):.4f}")

    old = np.load(f"{GT}/view_margin.npy").astype(np.float32)
    print(f"\nmargin comparison (higher = more decisive)")
    print(f"   single muddled axis (9 options): median {np.median(old):.4f}")
    for axis, (_, _, m) in out.items():
        print(f"   {axis:<12} median {np.median(m.astype(np.float32)):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
