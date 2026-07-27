# Vinted project — self-contained brief (read this to start cold)

> Companion project to **job-monitor** (`/home/malmasik/job-monitor`, github.com/aminalmasi/job-monitor).
> Same owner, same cluster. This brief + the assistant's memory = full context.

## ⚠️ DEPLOYMENT — RESOLVED (2026-07-27)
**The cluster CANNOT reach the DataImpulse proxy gateway** — `gw.dataimpulse.com` is refused on all ports (823 & 443) from `147.162.22.60` (GARR / Univ. Padova), i.e. the **university network blocks the proxy host**. The cluster stays only for job-monitor (its ATS polling needs no proxy).

**Chosen host: GitHub Actions** — verified working, repo `github.com/aminalmasi/vinted-watcher`:
| Check (runner `40.81.6.242`, Azure US) | Result |
| --- | --- |
| `gw.dataimpulse.com:823` | OPEN (`:443` refused — **use 823**) |
| exit IP via proxy | `87.13.116.149` — Italy, Veneto, Telecom Italia **residential** ✅ |
| `vinted.it` homepage via proxy | HTTP 200, 2.3 MB ✅ |
| `/api/v2/catalog/items?search_text=prada+shoes` | HTTP 200, 20 IT listings ✅ |

Secrets already set on the repo: `PROXY_URL`, `TELEGRAM_BOT_TOKEN`. Local copies: `~/.config/proxy.env`, `~/.config/vinted.env` (mode 600).

### GitHub Actions caveats to design around
- **Free minutes:** private repo = 2000 min/mo, billed per job rounded up. A 20-min poll ≈ 2160 runs/mo ≈ 4300 min → **over budget**. Either make the repo **public** (unlimited free minutes; secrets stay encrypted, only listing metadata becomes public) or poll **hourly** on private.
- **Cron drift:** scheduled workflows routinely fire 5–20 min late and can be skipped at peak. Fine for sold-detection, which is not latency-critical.
- **Auto-disable:** scheduled workflows are disabled after 60 days of repo inactivity — the per-run state commit keeps the repo active.
- **State:** commit `data/state.json` (tracked listings) back to the repo each run. No DB server needed.

### ⚠️ Proxy data budget (€5 PAYG is metered by GB)
The homepage bootstrap is **2.3 MB**; the search response is **0.43 MB**. Bootstrapping every run = ~2.7 MB × 72/day ≈ **5.8 GB/month** — would burn the credit fast.
**Fix: cache the `access_token_web` cookie in the state file** and only re-bootstrap on 401/403. That drops each poll to ~0.43 MB ≈ 0.9 GB/month.

## SPECIFIC GOAL (user, 2026-07-27) — first concrete watcher
Example search: **"Prada shoes" any size, on Vinted ITALY** (vinted.it — the user cares about listings shown to *them* in Italy).
- **Seed:** remember all listings published in the **last 5 days** matching the search — store by **description/metadata (title, price, brand, size, url, posted date)**, **NOT images** (to keep data low).
- **Poll every 20 minutes** through the Italy proxy; **update the list**: add NEW listings, and mark ones that **SOLD/disappeared**.
- **⚠️ ALERT RULE (user, 2026-07-27): Telegram gets SOLD items ONLY.** Do **not** push new/active listings. New listings are still tracked silently — they are the pool we later detect sales from — but they generate no message.
- Be careful about blocking (gentle, rotating residential proxy) and do NOT expose the cluster.

## HOW TO DETECT "SOLD" (probed 2026-07-27, settled)
The catalog feed **only ever returns live items** — there is no sold flag in the search payload. Verified on a real item:
- `status` is the **condition** ("Discrete"), *not* the sale state. `is_visible` is always `true`. `item_box` carries no sold badge.
- ⚠️ **Do not text-match "Venduto" on the item page** — a *live* item's page contains it 6× (and "venduto" 37×) as inert UI strings in the JS bundle. Guaranteed false positives.
- ✅ **Use the JSON embedded in the item page.** A live item shows `is_closed: false`, `is_hidden: false`, `is_reserved: false`, `item_closing_action: null`. A sold item flips `item_closing_action` / `is_closed`.
- `/api/v2/users/{id}/items` is **404** — the closet API is not at that path, don't rely on it.

**Resulting algorithm:** poll feed → item present = still live; item that *was* tracked and is now missing from the feed = **candidate** → fetch its item page and confirm:
- `item_closing_action` set / `is_closed: true` → **SOLD → Telegram**
- HTTP 404/410 → seller deleted it → silent
- still live → it merely fell off the paged feed → keep tracking

⚠️ The feed is ordered `newest_first` and we read only ~190 listings, so listings
drop out of our window by **ageing**, not only by selling. The watcher therefore
compares a missing listing against the feed's **age floor** (oldest `photo_ts`
seen this run): missing *and newer than the floor* = genuinely gone → confirm;
missing *and older* = merely aged out → re-check on a slow cadence. If any feed
page fails, the floor is distrusted for that run.

## STATUS 2026-07-27 (end of session)
**Working:** proxy → catalog feed → parse → dedupe → state committed to git.
~190 listings tracked per run, ~580 KB metered traffic per run, no retries.

**⛔ BLOCKED — the confirmation step.** `https://www.vinted.it/items/{id}` now
returns **HTTP 403** for every request from our proxy exits, even with full
browser navigation headers, 4 s spacing, and exit rotation. It worked at 14:52
and has 403'd consistently since ~15:00, so it is reputation/bot-detection on
the **HTML** path (the JSON catalog API from the same exits is fine).
The watcher fails **safe**: an unconfirmable listing returns `unknown`, stays
tracked, and never produces an alert. So it under-reports; it never lies.

**Ideas to unblock, cheapest first:**
1. Re-bootstrap a *fresh* homepage token immediately before item-page fetches —
   the one success came right after a bootstrap, the 403s came on a 35-min-old token.
2. Retry `/api/v2/items/{id}` (JSON) with the anon token + full browser headers;
   the JSON path is not being blocked the way HTML is.
3. Drop confirmation entirely: treat "vanished while newer than the age floor,
   still absent after N consecutive polls" as sold. Zero extra requests, but it
   cannot tell a sale from a seller deletion — would need saying so in the alert.
4. Add a sticky-session DataImpulse port so one exit IP builds reputation
   instead of hopping every request.

**Proxy flakiness (resolved enough):** `gw.dataimpulse.com` returns a varying set
of A records (seen: `69.67.149.191`/`185.209.176.103`, later `64.34.81.65/89/101`)
and goes through multi-minute windows where *all* of them refuse connections.
Retries rotate across every resolved gateway; a run that still fails aborts
without touching state, so nothing is corrupted and the next run recovers.

**⚠️ CREDENTIAL EXPOSURE (2026-07-27 ~14:49–14:51):** a diagnostic step ran
`curl -v` through the proxy, and the `Proxy-Authorization: Basic <base64>` header
was printed into an Actions log while the repo was briefly public. GitHub masks
`PROXY_URL` but not its base64 encoding. The run log was deleted and the repo set
back to private within ~2 min. **The DataImpulse password should be rotated**, and
the `PROXY_URL` secret + `~/.config/proxy.env` updated. Never run `curl -v`
through the proxy in CI again.

**Repo visibility:** currently **private**, so the cron is **hourly** (`7 * * * *`)
to stay inside the 2000 free min/mo. The user chose public + 20-min polling; once
the proxy password is rotated, flip back to public and restore `*/20 * * * *`.

**Still needed from the user:** `TELEGRAM_CHAT_ID`. `getUpdates` is empty because
nobody has messaged the bot. Press Start on **t.me/vinted_ads_bot**, then read the
id from `getUpdates` and set it as a repo secret. Until then `notify.send()` logs
the message and drops it.

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
