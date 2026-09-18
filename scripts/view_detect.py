"""Tag every photo with its viewpoint, zero-shot, then let a human check it.

Training a view classifier needs labels nobody has yet. SigLIP2 is already an
image-text model and is already embedded over the whole corpus, so zero-shot
prompting costs one text-encoder pass instead of an annotation round. If it is
accurate enough on inspection, no classifier is needed at all; if it is not,
the verified sample becomes the training set for one.

Prompts are written as full captions rather than bare labels ("a photo of the
sole of a shoe", not "sole") because SigLIP was trained on caption-like text
and bare nouns sit off its text distribution.

Confidence is the margin between the top two views, not the top softmax: a
photo that scores 0.9 for "side" and 0.88 for "angled" is genuinely ambiguous,
and that is exactly the case a human should see.
"""
from __future__ import annotations
import json, os, sys
import numpy as np
import torch

DATA = os.path.expanduser("~/vestiaire_data")
HF = "/extra/malmasik/hf_models/SigLIP2/google_siglip2_so400m_patch16_384"
OUT = f"{DATA}/ground_truth/view_tags.npy"

VIEWS = {
    "side":    "a side profile photo of a single shoe",
    "pair":    "a photo of a pair of shoes side by side",
    "top":     "a photo of shoes seen from above, looking down",
    "back":    "a photo of the back and heel of a shoe",
    "sole":    "a photo of the sole and bottom tread of a shoe",
    "front":   "a photo of the front and toe of a shoe",
    "detail":  "a close-up photo of a detail, logo or buckle of a shoe",
    "worn":    "a photo of a person wearing the shoes on their feet",
    "boxed":   "a photo of a shoe box or packaging",
}


def main() -> int:
    from transformers import SiglipModel, SiglipProcessor
    dev = "cuda"
    model = SiglipModel.from_pretrained(HF).eval().to(dev)
    proc = SiglipProcessor.from_pretrained(HF)
    names = list(VIEWS)
    with torch.no_grad():
        tin = proc(text=[VIEWS[k] for k in names], padding="max_length",
                   return_tensors="pt").to(dev)
        T = model.get_text_features(**tin)
        # transformers 5.x returns a ModelOutput here, not a tensor - the same
        # trap that killed the image embeddings earlier. Handled in both places
        # now rather than patched where it happens to bite.
        if not torch.is_tensor(T):
            T = getattr(T, "text_embeds", None) or getattr(T, "pooler_output", None)
        T = torch.nn.functional.normalize(T.float(), dim=-1)
    print(f"{len(names)} view prompts encoded, dim {T.shape[1]}", flush=True)

    X = np.load(f"{DATA}/embeddings/siglip2.npy")
    paths = json.load(open(f"{DATA}/embeddings/siglip2_paths.json"))
    print(f"tagging {len(paths):,} photos", flush=True)
    tags = np.zeros(len(paths), dtype=np.int8)
    marg = np.zeros(len(paths), dtype=np.float16)
    B = 200_000
    for i in range(0, len(paths), B):
        V = torch.from_numpy(X[i:i+B]).to(dev).float()
        S = V @ T.T
        top2 = S.topk(2, dim=1)
        tags[i:i+B] = top2.indices[:, 0].cpu().numpy().astype(np.int8)
        marg[i:i+B] = (top2.values[:, 0] - top2.values[:, 1]).cpu().numpy()
        print(f"  {min(i+B, len(paths)):,}/{len(paths):,}", flush=True)
    np.save(OUT, tags)
    np.save(OUT.replace("view_tags", "view_margin"), marg)
    json.dump(names, open(f"{DATA}/ground_truth/view_names.json", "w"))
    import collections
    c = collections.Counter(tags.tolist())
    print("\nview distribution:")
    for k, v in c.most_common():
        print(f"   {names[k]:<9} {v:>8,}  {100*v/len(tags):5.1f}%   "
              f"median margin {np.median(marg[tags==k]):.3f}")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
