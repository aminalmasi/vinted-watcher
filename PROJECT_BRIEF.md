# Vinted project — self-contained brief (read this to start cold)

> Companion project to **job-monitor** (`/home/malmasik/job-monitor`, github.com/aminalmasi/job-monitor).
> Same owner, same cluster. This brief + the assistant's memory = full context.

## ⚠️ CRITICAL DEPLOYMENT FINDING (2026-07-27)
**The cluster CANNOT reach the DataImpulse proxy gateway** — `gw.dataimpulse.com` is refused on all ports (823 & 443) while other hosts/ports work fine, i.e. the **university network blocks the proxy host**. Therefore the Vinted watcher **must run OFF the cluster**: the user's **laptop** (dev) or a **cheap VPS ~€3-5/mo** (24/7 production) or possibly **GitHub Actions** (verify GH runner IPs can reach DataImpulse). The cluster stays only for job-monitor (its ATS polling needs no proxy). Proxy creds are in `~/.config/proxy.env`. **First: user tests the proxy from a non-cluster host:** `curl -x "http://9112e18e79fa2d66e83b__cr.it:PASS@gw.dataimpulse.com:823" http://ip-api.com/json` → expect an Italian IP.

## SPECIFIC GOAL (user, 2026-07-27) — first concrete watcher
Example search: **"Prada shoes" any size, on Vinted ITALY** (vinted.it — the user cares about listings shown to *them* in Italy).
- **Seed:** remember all listings published in the **last 5 days** matching the search — store by **description/metadata (title, price, brand, size, url, posted date)**, **NOT images** (to keep data low).
- **Poll every 20 minutes** through the Italy proxy; **update the list**: add NEW listings, and mark ones that **SOLD/disappeared**.
- **Report** new + sold to Telegram (a dedicated Vinted bot/chat). Be careful about blocking (gentle, rotating residential proxy) and do NOT expose the cluster.

## Goal
Two capabilities:
1. **Monitor saved Vinted searches (Italy)** for NEW listings → Telegram alert (a **separate Vinted bot/chat**, not the job-monitor one).
2. **Price-comparison / arbitrage:** for a Vinted listing, find the same/similar item on **eBay (+ optionally Google/Bing)**, compare prices, and flag listings priced **well below market** (good flips).

## Owner / environment
- ML/CV researcher at Univ. of Padova. Cluster = Padova Math HPC (see assistant memory `cluster-setup`). Submit from labsrv7; SLURM; venv `/home/malmasik/.venvs/a100_new_real/bin/python`; GPUs via SLURM. Cached embedding models in `~/hf_models` (SigLIP2, DINOv3, CLIP, VLM2Vec, **Qwen3-VL-Embedding**) — reuse for visual matching.
- Never delete files without asking (memory `feedback-safety`).

## Proxy (bought 2026-07-27)
- **DataImpulse residential, €5 PAYG, country = Italy, rotating.** Traffic non-expiring. Vinted/eBay/Google/LinkedIn all allowed (checked their blocklist).
- **Credentials shared location:** `~/.config/proxy.env` (a single `JOBTOOLS_PROXY_URL=http://user:pass@gateway:port` line; country=Italy encoded in the username, DataImpulse format `user__cr.it`). Both job-monitor and vinted read it.
- job-monitor's `jobmon/http.py` already has **opt-in proxy support** — copy that pattern.

## Architecture (reuse job-monitor skeleton)
Same shape: **scheduler(SLURM self-perpetuating) → fetch → dedupe(`seen` table) → classify → Telegram.** Copy from job-monitor: `http.py` (proxy-aware client), `notify.py` (Telegram), `db.py` (SQLite `seen` dedup), scheduler sbatch patterns, single-writer `write_guard`, NFS-safe DB (journal_mode=DELETE).

### Data-volume rule (important for the €5 proxy)
- **Detection (Vinted catalog JSON)** → **through the proxy** (Vinted blocks datacenter IPs). Tiny (<1 GB/mo).
- **Images** → **download DIRECT, NOT through the proxy** (Vinted CDN `images*.vinted.net` is far less IP-sensitive). Keeps proxy GB near-zero even when downloading images for visual matching.

### Vinted detection
- Each saved search has an internal JSON feed: `https://www.vinted.it/api/v2/catalog/items?search_text=...&catalog_ids=...&price_to=...&order=newest_first&per_page=...` (needs headers/cookies; Vinted uses an anon token — fetch it from the homepage first, or use the public catalog endpoint). Poll via proxy every ~1h. Dedupe by Vinted listing `id`.

### Price-comparison pipeline (the "model")
```
Vinted item → LLM extract (brand/model/attrs) → build query
   → eBay Browse API (active + SOLD listings = real market value)   [free, 5k/day]
   → [optional] Bing Visual Search (reverse-image) / Google Shopping via SerpAPI
   → VISUAL VERIFY: embed Vinted image + candidate images with SigLIP2/CLIP (cached, GPU),
     cosine similarity → keep true same-product matches
   → price stats (median/range of matched comps)
   → if Vinted price << market median → Telegram arbitrage alert (item, price, comp median, %below, links)
```
- **Start minimal:** eBay sold-comps + SigLIP visual verify = highest signal, ~€0 extra. Add Google/Bing later.

## Telegram
- Create a **new bot** via @BotFather for Vinted (or a channel), get token + chat_id (press Start). Keep separate from job-monitor's `find_job_amin_asma_bot`.

## Phased plan (start simple!)
- **Phase 0:** minimal watcher — ONE saved search → detect new listings (via proxy) → Telegram. Prove the proxy + dedupe + alert chain.
- **Phase 1:** multiple saved searches; store listing data (title/price/brand/url/images-direct).
- **Phase 2:** eBay Browse API comps + SigLIP visual matching → price stats.
- **Phase 3:** arbitrage alerts (Vinted price vs market median), tune thresholds.
- **Phase 4:** deploy self-perpetuating on cluster (or off-IP), like job-monitor's `slurm_auto.sbatch`.

## First step when resuming
"Read this brief. I bought DataImpulse (Italy). Let's do Phase 0: minimal Vinted watcher for one saved search through the proxy → new bot." Then set up `~/.config/proxy.env`, a new Telegram bot, and copy the job-monitor skeleton into `/home/malmasik/vinted/`.
