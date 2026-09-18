"""Run BiRefNet over every eBay photo and show the lot.

SAM2 is dropped as a referee. Its low agreement on busy backgrounds turned out
to say more about a centre-point prompt (which can grab one shoe of a pair, or
a single strap) than about BiRefNet, so keeping it would just add noise to the
page. In its place: cheap automatic flags that catch the failure modes an eye
would catch anyway, so suspicious masks surface at the top instead of being
buried in a long scroll.

  fg < 3%      the mask collapsed - almost certainly a miss
  fg > 85%     the mask took the whole frame - likely inverted or leaked
  border > 25% foreground hugging the image edge, which a cut-out object
               should not do, so the background probably leaked in

Sorted busiest-background first, because that is where it should break.
"""
from __future__ import annotations
import base64, glob, io, os, sys
import numpy as np
import torch
from PIL import Image

DATA = os.path.expanduser("~/vestiaire_data")
OUT = f"{DATA}/ground_truth/segmentation_ebay.html"
MASKS = f"{DATA}/masks_ebay"
SIZE, THUMB = 1024, 150


def b64(im, size=THUMB):
    im = im.copy(); im.thumbnail((size, size))
    b = io.BytesIO(); im.convert("RGB").save(b, "JPEG", quality=70)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


def main() -> int:
    from transformers import AutoModelForImageSegmentation
    import torchvision.transforms as T
    dev = "cuda"
    bi = AutoModelForImageSegmentation.from_pretrained(
        "ZhengPeng7/BiRefNet", trust_remote_code=True).eval().to(dev).half()
    tf = T.Compose([T.Resize((SIZE, SIZE)), T.ToTensor(),
                    T.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
    os.makedirs(MASKS, exist_ok=True)

    files = sorted(glob.glob(f"{DATA}/ebay/*/*.jpg"))
    print(f"{len(files)} eBay photos", flush=True)
    rows = []
    for i, path in enumerate(files, 1):
        try:
            im = Image.open(path).convert("RGB")
        except Exception:
            continue
        W, H = im.size
        with torch.no_grad():
            pred = bi(tf(im).unsqueeze(0).to(dev).half())[-1].sigmoid().float().cpu()[0, 0]
        m = np.array(Image.fromarray((pred.numpy() * 255).astype(np.uint8))
                     .resize((W, H))) > 127
        Image.fromarray((m * 255).astype(np.uint8)).save(
            f"{MASKS}/{os.path.basename(path)[:-4]}.png")

        a = np.asarray(im).astype(np.float32)
        ring = np.ones((H, W), bool)
        ring[int(H*.06):int(H*.94), int(W*.06):int(W*.94)] = False
        white = float((a[ring] > 235).all(axis=1).mean())
        fg, bf = float(m.mean()), float(m[ring].mean())
        flags = []
        if fg < 0.03: flags.append("mask collapsed")
        if fg > 0.85: flags.append("mask took the frame")
        if bf > 0.25: flags.append("touching border")
        cut = np.asarray(im).copy(); cut[~m] = 255
        rows.append({"white": white, "fg": fg, "bf": bf, "flags": flags,
                     "orig": b64(im), "cut": b64(Image.fromarray(cut)),
                     "name": os.path.basename(path)})
        if i % 60 == 0:
            print(f"  {i}/{len(files)}", flush=True)

    rows.sort(key=lambda r: (not r["flags"], r["white"]))
    flagged = sum(1 for r in rows if r["flags"])
    cards = "".join(
        f'<figure class="{"bad" if r["flags"] else ""}">'
        f'<div class=p><img src="{r["orig"]}"><img src="{r["cut"]}"></div>'
        f'<figcaption>bg {r["white"]*100:.0f}% white · shoe {r["fg"]*100:.0f}%'
        + (f'<br><b>{", ".join(r["flags"])}</b>' if r["flags"] else "")
        + '</figcaption></figure>' for r in rows)
    html = f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>BiRefNet on eBay</title>
<style>:root{{color-scheme:light dark;--bg:#f6f5f3;--card:#fff;--tx:#171717;--dim:#6f6f6f;
--line:#e4e1dc;--bad:#c0392b}}
@media(prefers-color-scheme:dark){{:root{{--bg:#121212;--card:#1d1d1d;--tx:#ececec;
--dim:#9a9a9a;--line:#2c2c2c;--bad:#ff6b5c}}}}
body{{margin:0;background:var(--bg);color:var(--tx);font:13px/1.5 ui-sans-serif,-apple-system,Arial}}
header{{padding:16px 20px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg)}}
h1{{margin:0;font-size:18px}} .sub{{color:var(--dim);font-size:13px;margin-top:6px;max-width:78ch}}
.grid{{display:grid;gap:12px;padding:16px 20px;
grid-template-columns:repeat(auto-fill,minmax(316px,1fr))}}
figure{{margin:0;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px}}
figure.bad{{border-color:var(--bad);border-width:2px}}
.p{{display:flex;gap:6px}} .p img{{width:50%;border-radius:6px;display:block;background:#fff}}
figcaption{{font-size:11px;color:var(--dim);margin-top:5px}}
figure.bad figcaption b{{color:var(--bad)}}</style></head><body>
<header><h1>BiRefNet on all {len(rows)} eBay photos</h1>
<div class=sub>Left: original. Right: cut-out on white. Sorted with automatically
<b>flagged</b> masks first, then busiest background first &mdash; so the hardest and most
suspect cases are at the top. {flagged} of {len(rows)} were flagged
({100*flagged/max(len(rows),1):.0f}%). Vestiaire is excluded on purpose: 80% of its photos
already have white borders, which makes any segmenter look good.</div></header>
<div class=grid>{cards}</div></body></html>"""
    open(OUT, "w", encoding="utf-8").write(html)
    fgs = np.array([r["fg"] for r in rows])
    print(f"\nshoe-pixel fraction: mean {fgs.mean():.3f}, "
          f"p10 {np.percentile(fgs,10):.3f}, p90 {np.percentile(fgs,90):.3f}")
    print(f"flagged: {flagged}/{len(rows)} ({100*flagged/len(rows):.1f}%)")
    print(f"masks -> {MASKS}")
    print(f"wrote {OUT} ({os.path.getsize(OUT)/1024/1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
