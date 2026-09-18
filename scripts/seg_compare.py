"""BiRefNet vs SAM2 on the same adversarial sample, with agreement as a QC signal.

Two architecturally unrelated models are used on purpose:

  BiRefNet  dichotomous segmentation - answers "which pixels are the object"
            with no prompt, and is built for fine structures (straps, buckles,
            heel gaps), which is exactly where our hard cases live.
  SAM2      promptable and class-agnostic. It does NOT know what the salient
            object is, so it needs a prompt; a full-image box is the fairest
            automatic proxy. Included as an independent second opinion rather
            than as a contender.

Where two unrelated models agree on a mask, that is real evidence it is right.
Their IoU therefore gives a label-free quality signal that scales: trust the
agreements, send only the disagreements to a human eye.

The failed clothes-parser and a not-near-white threshold are kept as floors, so
the comparison is anchored rather than self-congratulatory.
"""
from __future__ import annotations
import base64, glob, io, json, os, random, sys
import numpy as np
import torch
from PIL import Image

DATA = os.path.expanduser("~/vestiaire_data")
OUT = f"{DATA}/ground_truth/segmentation_compare.html"
SIZE = 1024


def pick():
    """eBay only, hardest first.

    Vestiaire is the easy case by construction - 80% of sold photos already
    have near-white borders, so a segmenter can look excellent there while
    being useless on a real background. eBay is where backgrounds actually
    exist, so the sample is drawn from it and ORDERED by how busy the border
    is: the least-white images come first. This is the adversarial end of the
    distribution, not a representative draw, and the numbers should be read
    that way.
    """
    import numpy as _np
    from PIL import Image as _Image
    scored = []
    for p in glob.glob(f"{DATA}/ebay/*/*.jpg"):
        try:
            a = _np.asarray(_Image.open(p).convert("RGB").resize((96, 96))).astype(_np.float32)
        except Exception:
            continue
        m = _np.ones((96, 96), bool); m[10:86, 10:86] = False
        white = float((a[m] > 235).all(axis=1).mean())
        scored.append((white, p))
    scored.sort()                       # busiest backgrounds first
    hard = [(p, f"ebay busy bg (border {w*100:.0f}% white)") for w, p in scored[:16]]
    rest = scored[len(scored)//2:]
    random.seed(11)
    mid = [(p, f"ebay mid (border {w*100:.0f}% white)")
           for w, p in random.sample(rest, min(8, len(rest)))]
    return hard + mid


def b64(im, size=190):
    im = im.copy(); im.thumbnail((size, size))
    b = io.BytesIO(); im.convert("RGB").save(b, "JPEG", quality=76)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


def cut(im, mask):
    a = np.asarray(im).copy(); a[~mask] = 255
    return Image.fromarray(a)


def main() -> int:
    from transformers import (AutoModelForImageSegmentation, Sam2Model, Sam2Processor)
    import torchvision.transforms as T
    dev = "cuda"

    bi = AutoModelForImageSegmentation.from_pretrained(
        "ZhengPeng7/BiRefNet", trust_remote_code=True).eval().to(dev).half()
    tf = T.Compose([T.Resize((SIZE, SIZE)), T.ToTensor(),
                    T.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])

    sam = Sam2Model.from_pretrained("facebook/sam2.1-hiera-large").eval().to(dev)
    sproc = Sam2Processor.from_pretrained("facebook/sam2.1-hiera-large")

    rows, ious, bfr, sfr = [], [], [], []
    for path, tag in pick():
        try:
            im = Image.open(path).convert("RGB")
        except Exception:
            continue
        W, H = im.size

        with torch.no_grad():
            x = tf(im).unsqueeze(0).to(dev).half()
            pred = bi(x)[-1].sigmoid().float().cpu()[0, 0]
        m_bi = np.array(Image.fromarray((pred.numpy() * 255).astype(np.uint8))
                        .resize((W, H))) > 127

        # SAM2 needs a prompt. A full-image box is degenerate - it asserts the
        # object fills the frame, and SAM2 duly returned the BACKGROUND, giving
        # IoU 0.000 against BiRefNet on all 24 images. Two masks covering 34%
        # and 60% of one image cannot be disjoint, which is how the bug showed.
        # A centre point is the honest automatic proxy: it says "the thing here".
        pts = [[[[W / 2.0, H / 2.0]]]]
        lbl = [[[1]]]
        with torch.no_grad():
            si = sproc(images=im, input_points=pts, input_labels=lbl,
                       return_tensors="pt").to(dev)
            so = sam(**si, multimask_output=True)
            masks = sproc.post_process_masks(so.pred_masks.cpu(),
                                             si["original_sizes"])[0][0].numpy() > 0
            scores = so.iou_scores.cpu().numpy().reshape(-1)
        m_sam = masks[int(scores.argmax())]
        # Polarity guard: a foreground mask should not hug the image border.
        ring = np.ones((H, W), bool)
        ring[int(H * .1):int(H * .9), int(W * .1):int(W * .9)] = False
        if m_sam[ring].mean() > 0.5:
            m_sam = ~m_sam

        inter = (m_bi & m_sam).sum(); union = (m_bi | m_sam).sum()
        iou = float(inter / union) if union else 0.0
        ious.append(iou); bfr.append(float(m_bi.mean())); sfr.append(float(m_sam.mean()))

        a = np.asarray(im).astype(np.float32)
        thr = ~((a > 235).all(axis=2))
        rows.append(f'''<section><h3>{tag} &mdash; BiRefNet {m_bi.mean()*100:.0f}% ·
          SAM2 {m_sam.mean()*100:.0f}% · <b>IoU {iou:.2f}</b></h3><div class=r>
          <figure><img src="{b64(im)}"><figcaption>original</figcaption></figure>
          <figure><img src="{b64(cut(im,m_bi))}"><figcaption>BiRefNet</figcaption></figure>
          <figure><img src="{b64(cut(im,m_sam))}"><figcaption>SAM2 (box)</figcaption></figure>
          <figure><img src="{b64(cut(im,thr))}"><figcaption>white threshold</figcaption></figure>
          </div></section>''')

    html = f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Segmentation comparison</title>
<style>:root{{color-scheme:light dark;--bg:#f6f5f3;--tx:#171717;--dim:#6f6f6f;--line:#e4e1dc}}
@media(prefers-color-scheme:dark){{:root{{--bg:#121212;--tx:#ececec;--dim:#9a9a9a;--line:#2c2c2c}}}}
body{{margin:0;background:var(--bg);color:var(--tx);font:14px/1.5 ui-sans-serif,-apple-system,Arial}}
header{{padding:16px 20px;border-bottom:1px solid var(--line)}} h1{{margin:0;font-size:18px}}
.sub{{color:var(--dim);font-size:13px;margin-top:6px;max-width:70ch}}
section{{padding:12px 20px;border-bottom:1px solid var(--line)}}
h3{{margin:0 0 8px;font-size:12.5px;font-weight:600;color:var(--dim)}}
.r{{display:flex;gap:10px;flex-wrap:wrap}} figure{{margin:0}}
figure img{{width:190px;border-radius:8px;border:1px solid var(--line);display:block;background:#fff}}
figcaption{{font-size:11px;color:var(--dim);text-align:center;margin-top:3px}}</style></head><body>
<header><h1>BiRefNet vs SAM2 &mdash; does it clip the shoe?</h1>
<div class=sub>Deliberately hard sample: strappy sandals, boots, clogs, and eBay photos with real
backgrounds. Look for clipped straps, missing heels, and whether the cutout keeps holes between
straps. <b>IoU</b> is agreement between the two models &mdash; low values mark images that would
need a human eye if we ran this at scale.</div></header>
{''.join(rows)}</body></html>"""
    open(OUT, "w", encoding="utf-8").write(html)
    ious = np.array(ious)
    print(f"\nimages: {len(ious)}")
    print(f"BiRefNet foreground: mean {np.mean(bfr):.3f}, empty {sum(1 for f in bfr if f<0.01)}")
    print(f"SAM2     foreground: mean {np.mean(sfr):.3f}, empty {sum(1 for f in sfr if f<0.01)}")
    print(f"IoU agreement: mean {ious.mean():.3f}, median {np.median(ious):.3f}, "
          f"<0.7: {(ious<0.7).sum()}/{len(ious)}")
    print(f"wrote {OUT} ({os.path.getsize(OUT)/1024/1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
