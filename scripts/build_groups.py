"""Build product-group ground truth from listing slugs.

The equivalence class is (brand, model, subcategory) — deliberately NOT
(brand, model) alone, because a model token names a product LINE rather than a
product: "rockstud" alone spans 4,935 live listings across flats, pumps,
sandals and boots over a decade. Splitting by subcategory is the tightest
grouping the metadata supports.

Colour and material are stripped by construction, which is the requested
behaviour: a black and a beige pair of the same shoe belong together.

Sources, and why the slug rather than the `model` field: apiv2 populates
`model` on only ~4% of listings, and where it IS populated the slug recovers
it 94% of the time. The slug additionally yields a model token for 28-43% of
listings, so it is both broader and nearly as accurate.

Everything here comes from metadata already on disk — no network at all.
"""

from __future__ import annotations

import collections, glob, json, os, re, sys, unicodedata

DATA = os.path.expanduser("~/vestiaire_data")
OUT = os.path.join(DATA, "ground_truth")

MATERIAL = {"pelle","scamosciato","tela","vernice","gomma","velluto","raso","seta",
 "sintetico","tessuto","cuoio","camoscio","plastica","metallo","metallizzato","pitone",
 "coccodrillo","struzzo","lucertola","pelliccia","shearling","lino","cotone","lana",
 "juta","paglia","rafia","glitter","paillette","strass","tweed","denim","jeans",
 "vegana","specificato","dacqua","acqua","vitello","cavallino","pony","nappa","nabuk",
 "suede","poliestere","poliuretano","microfibra","ecopelle","caucciu","corda","feltro",
 "maglia","neoprene","anacardi","tela-di-jeans","gomma-e-plastica"}
COLOUR = {"nero","bianco","beige","marrone","blu","multicolore","rosa","rosso","altro",
 "grigio","argentato","dorato","verde","cammello","bordeaux","viola","giallo","ecru",
 "arancione","marina","kaki","turchese","fucsia","corallo","antracite","panna","avorio",
 "tortora","porpora","celeste","salmone","senape"}
SUBWORD = {"donna","uomo","scarpe","col","tacco","da","ginnastica","sandali","stivali",
 "stivaletti","mocassini","ballerine","zoccoli","espadrillas","derby","scarpette",
 "infradito","ciabatte","alla","schiava","con","tacchi","alti","basse","semi","alto",
 "stringate","sabot","mules"}
BRANDWORD = {"gucci","prada","chanel","hermes","dior","christian","louboutin","saint",
 "laurent","valentino","garavani","golden","goose","bottega","veneta","miu","maison",
 "martin","margiela","salvatore","ferragamo","yves"}
STOP = MATERIAL | COLOUR | SUBWORD | BRANDWORD | {"di","in","con","e","non","effetto","x",
 "the","and","con","per","del","della"}

BRANDS = {"2":"Gucci","50":"Chanel","14":"Hermès","236":"Christian Louboutin",
 "10":"Dior","60":"Prada","3119":"Saint Laurent","88":"Valentino Garavani",
 "809":"Golden Goose","115":"Bottega Veneta"}
MIN_GROUP = int(os.environ.get("MIN_GROUP", "4"))
CROSS_BRAND_MAX = int(os.environ.get("CROSS_BRAND_MAX", "3"))


def model_of(link: str) -> str:
    slug = link.split("/")[-1].replace(".shtml", "")
    toks = [t for t in slug.split("-")
            if not t.isdigit() and t not in STOP and len(t) > 1]
    return "-".join(toks)


def sub_of(link: str) -> str:
    parts = link.strip("/").split("/")
    return parts[1] if len(parts) > 2 else ""


def index_images() -> dict:
    """listing id -> list of image paths, for both corpora."""
    idx = collections.defaultdict(list)
    for p in glob.glob(f"{DATA}/images/*/*.jpg"):
        idx[os.path.basename(p)[:-4]].append(os.path.relpath(p, DATA))
    for p in glob.glob(f"{DATA}/live/*/*.jpg"):
        idx[os.path.basename(p).split("_")[0]].append(os.path.relpath(p, DATA))
    return idx


def main() -> int:
    imgs = index_images()
    print(f"image index: {len(imgs):,} listings with at least one photo")

    rows = []
    for corpus, pat in (("sold", f"{DATA}/meta/*.jsonl"), ("live", f"{DATA}/live_meta/*.jsonl")):
        for f in glob.glob(pat):
            for line in open(f, encoding="utf-8"):
                r = json.loads(line)
                if not r.get("link"):
                    continue
                r["corpus"] = corpus
                rows.append(r)
    print(f"listings: {len(rows):,}")

    groups = collections.defaultdict(list)
    for r in rows:
        m = model_of(r["link"])
        if not m:
            continue
        groups[(str(r["brand"]), m, sub_of(r["link"]))].append(r)

    # Two passes, because the first version compared whole token strings and so
    # only caught bare material words. "cassandra-vinile", "tribute-anguilla"
    # and "anja-pelli-esotiche" survived it and fragmented their real groups
    # into singletons. Detecting cross-brand words at the WORD level and
    # stripping them lets those merge back into cassandra / tribute / anja.
    word_brands = collections.defaultdict(set)
    for (b_, m, _s) in groups:
        for w in m.split("-"):
            word_brands[w].add(b_)
    junk_words = {w for w, bs in word_brands.items() if len(bs) >= CROSS_BRAND_MAX}
    if junk_words:
        print(f"stripping {len(junk_words)} cross-brand WORDS: "
              f"{', '.join(sorted(junk_words)[:12])}")
        merged = collections.defaultdict(list)
        for (b_, m, sub), v in groups.items():
            m2 = "-".join(w for w in m.split("-") if w not in junk_words)
            if m2:
                merged[(b_, m2, sub)].extend(v)
        before, groups = len(groups), dict(merged)
        print(f"  groups {before:,} -> {len(groups):,} after merging fragments")

    # A real model name is brand-exclusive: "oran" is Hermes, "so-kate" is
    # Louboutin. A token appearing across many brands is therefore almost
    # certainly a material or style word my stop-list missed - this caught
    # pelli-esotiche, pizzo, mouton, vinile, sintetica, serpente, spugna,
    # alligatore and visone (2.6% of listings). Applied as a rule rather than a
    # hand-written list so it keeps working as the corpus grows.
    brands_per = collections.defaultdict(set)
    for (b, m, _sub) in groups:
        brands_per[m].add(b)
    cross = {m for m, bs in brands_per.items() if len(bs) >= CROSS_BRAND_MAX}
    if cross:
        print(f"dropping {len(cross)} cross-brand tokens (not model names): "
              f"{', '.join(sorted(cross)[:10])}")
    groups = {k: v for k, v in groups.items() if k[1] not in cross}

    kept = {k: v for k, v in groups.items() if len(v) >= MIN_GROUP}
    os.makedirs(OUT, exist_ok=True)
    n_img = 0
    with open(f"{OUT}/groups.jsonl", "w", encoding="utf-8") as fh:
        for gid, ((b, m, sub), members) in enumerate(
                sorted(kept.items(), key=lambda kv: -len(kv[1])), 1):
            items = []
            for r in members:
                paths = imgs.get(str(r["id"]), [])
                n_img += len(paths)
                items.append({"id": r["id"], "corpus": r["corpus"],
                              "price": r.get("price"), "cond": r.get("cond"),
                              "name": r.get("name"), "images": sorted(paths)})
            fh.write(json.dumps({"group_id": gid, "brand_id": b,
                                 "brand": BRANDS.get(b, b), "model": m,
                                 "subcategory": sub, "n": len(items),
                                 "items": items}, ensure_ascii=False) + "\n")

    sizes = sorted(len(v) for v in kept.values())
    withimg = sum(1 for k, v in kept.items()
                  for r in v if imgs.get(str(r["id"])))
    print(f"\ngroups (>= {MIN_GROUP} members): {len(kept):,}")
    print(f"  listings in groups : {sum(len(v) for v in kept.values()):,}")
    print(f"  with >=1 image     : {withimg:,}")
    print(f"  images referenced  : {n_img:,}")
    print(f"  group size: median {sizes[len(sizes)//2]}, "
          f"p90 {sizes[int(len(sizes)*.9)]}, max {sizes[-1]}")
    print(f"\nwrote {OUT}/groups.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
