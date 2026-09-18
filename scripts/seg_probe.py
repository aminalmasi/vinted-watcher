"""Can we actually segment shoes cleanly? Test on the cases most likely to fail.

Background removal is only worth doing if the mask is reliable, and the danger
is a segmenter that works on simple pumps but clips straps, heels or fur. That
failure is content-correlated, so it would inject a bias that looks like a
result. The sample is therefore deliberately adversarial: strappy sandals,
boots, and eBay photos with real backgrounds, not a random draw.

Two models compared:
  segformer_b2_clothes  has explicit Left-shoe / Right-shoe classes, but was
                        trained on people WEARING clothes - product shots are
                        out of distribution and that is the thing to check.
  luminance threshold   a floor, not a contender: Vestiaire is mostly white
                        backgrounds, so a trivial method should do well there
                        and fail on eBay. If the learned model cannot beat it,
                        that is worth knowing.
"""
from __future__ import annotations
import base64, glob, io, json, os, random, sys
import numpy as np
import torch
from PIL import Image

DATA = os.path.expanduser("~/vestiaire_data")
OUT = f"{DATA}/ground_truth/segmentation.html"
N = int(os.environ.get("N_IMAGES", "24"))


def pick_samples():
    """Hard cases on purpose: straps, boots, and real backgrounds."""
    gs = [json.loads(l) for l in open(f"{DATA}/ground_truth/groups.jsonl", encoding="utf-8")]
    want = {"sandali": 6, "stivali": 4, "scarpette": 3, "zoccoli": 3}
    out = []
    random.seed(11)
    for sub, k in want.items():
        cands = [i["images"][0] for g in gs if g["subcategory"] == sub
                 for i in g["items"] if i["images"]]
        out += [(os.path.join(DATA, p), f"vestiaire/{sub}") for p in random.sample(cands, min(k, len(cands)))]
    eb = glob.glob(f"{DATA}/ebay/*/*.jpg")
    out += [(p, "ebay") for p in random.sample(eb, min(8, len(eb)))]
    return out[:N]


def b64(im, size=190):
    im = im.copy(); im.thumbnail((size, size))
    b = io.BytesIO(); im.convert("RGB").save(b, "JPEG", quality=75)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


def main() -> int:
    from transformers import SegformerImageProcessor, AutoModelForSemanticSegmentation
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    proc = SegformerImageProcessor.from_pretrained("mattmdjaga/segformer_b2_clothes")
    model = AutoModelForSemanticSegmentation.from_pretrained(
        "mattmdjaga/segformer_b2_clothes").eval().to(dev)

    rows, stats = [], []
    for path, tag in pick_samples():
        try:
            im = Image.open(path).convert("RGB")
        except Exception:
            continue
        with torch.no_grad():
            inp = proc(images=im, return_tensors="pt").to(dev)
            lg = model(**inp).logits
            up = torch.nn.functional.interpolate(lg, size=im.size[::-1],
                                                 mode="bilinear", align_corners=False)
            seg = up.argmax(1)[0].cpu().numpy()
        mask = np.isin(seg, [9, 10])                      # left/right shoe
        frac = float(mask.mean())

        # trivial baseline: anything that is not near-white
        a = np.asarray(im).astype(np.float32)
        thr = ~((a > 235).all(axis=2))
        tfrac = float(thr.mean())

        cut = np.asarray(im).copy()
        cut[~mask] = 255
        overlay = np.asarray(im).astype(np.float32).copy()
        overlay[mask] = overlay[mask] * 0.55 + np.array([255, 90, 60]) * 0.45

        stats.append((tag, frac, tfrac))
        rows.append(f'''<section><h3>{tag} &mdash; shoe pixels: <b>{frac*100:.1f}%</b>
          (threshold baseline {tfrac*100:.1f}%)</h3><div class=r>
          <figure><img src="{b64(im)}"><figcaption>original</figcaption></figure>
          <figure><img src="{b64(Image.fromarray(overlay.astype(np.uint8)))}"><figcaption>mask</figcaption></figure>
          <figure><img src="{b64(Image.fromarray(cut))}"><figcaption>cutout</figcaption></figure>
          <figure><img src="{b64(Image.fromarray(np.where(thr[...,None], np.asarray(im), 255).astype(np.uint8)))}"><figcaption>threshold</figcaption></figure>
          </div></section>''')

    html = f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Shoe segmentation</title>
<style>:root{{color-scheme:light dark;--bg:#f6f5f3;--tx:#171717;--dim:#6f6f6f;--line:#e4e1dc}}
@media(prefers-color-scheme:dark){{:root{{--bg:#121212;--tx:#ececec;--dim:#9a9a9a;--line:#2c2c2c}}}}
body{{margin:0;background:var(--bg);color:var(--tx);font:14px/1.5 ui-sans-serif,-apple-system,Arial}}
header{{padding:16px 20px;border-bottom:1px solid var(--line)}} h1{{margin:0;font-size:18px}}
.sub{{color:var(--dim);font-size:13px;margin-top:4px}}
section{{padding:12px 20px;border-bottom:1px solid var(--line)}}
h3{{margin:0 0 8px;font-size:13px;font-weight:600;color:var(--dim)}}
.r{{display:flex;gap:10px;flex-wrap:wrap}} figure{{margin:0}}
figure img{{width:190px;border-radius:8px;border:1px solid var(--line);display:block}}
figcaption{{font-size:11px;color:var(--dim);text-align:center;margin-top:3px}}</style></head><body>
<header><h1>Shoe segmentation: does it clip the product?</h1>
<div class=sub>Deliberately hard sample &mdash; strappy sandals, boots, and eBay photos with real
backgrounds. Look for clipped straps, missing heels, and whether the eBay rows work at all.
The last column is a trivial not-near-white threshold, shown as a floor.</div></header>
{''.join(rows)}</body></html>"""
    open(OUT, "w", encoding="utf-8").write(html)
    ve = [f for t, f, _ in stats if t.startswith("vestiaire")]
    eb = [f for t, f, _ in stats if t == "ebay"]
    print(f"vestiaire shoe-pixel fraction: mean {np.mean(ve):.3f}, "
          f"empty masks {sum(1 for f in ve if f < 0.01)}/{len(ve)}")
    if eb:
        print(f"ebay      shoe-pixel fraction: mean {np.mean(eb):.3f}, "
              f"empty masks {sum(1 for f in eb if f < 0.01)}/{len(eb)}")
    print(f"wrote {OUT} ({os.path.getsize(OUT)/1024/1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
