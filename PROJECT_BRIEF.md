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
Sizes are *decompressed*; the metered figure is gzipped, roughly 7x smaller.
Homepage 2.3 MB, a 24-item feed page ~110 KB, an **item page 2.4 MB (~340 KB
metered)** — item pages dominate, which is why confirmations are rationed.
Measured steady state: **~575 KB metered per run** ≈ 415 MB/month hourly.
The anon token is cached in the state file so most runs skip the homepage.

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

⚠️ A listing missing from one poll is usually just search churn, not a sale —
see finding 3 below for how "really gone" is decided.

## STATUS 2026-07-27 — WORKING END TO END
Feed → parse → dedupe → **confirmation** → state committed to git. Verified in
production: 15/15 item pages fetched and their state JSON parsed correctly.
~189 listings tracked, **~575 KB metered traffic per run** (~415 MB/month hourly).
No SOLD alert has fired yet — that needs a tracked listing to actually sell,
which happens on its own schedule.

Telegram is live: **@vinted_ads_bot**, chat_id `276987728`, secret set, test
message delivered.

### Three findings that shaped the design
1. **`gw.dataimpulse.com` alternates between two DNS answer sets and one is dead.**
   Across nine runs, every run resolving to `185.209.176.103` / `69.67.149.191`
   succeeded; every run resolving to the `64.34.81.x` block failed on *all*
   addresses with `RemoteDisconnected`. Resolution is stable within a process, so
   an unlucky run was doomed from its first request. `KNOWN_GOOD_GATEWAYS` in
   `client.py` pins the working addresses and keeps DNS as a fallback.
2. **A 403 on an item page means "stale session", not "blocked".** Vinted serves
   the page happily to a session that has *just* loaded the homepage. A run of
   solid 403s turned into HTTP 200 immediately after a fresh bootstrap, so the
   watcher re-bootstraps before the confirmation phase and again on any 403,
   with `vinted.com` as a last resort.
3. **`photo_ts` is useless for deciding "aged out of the feed".** The feed's
   oldest photo is ~8 weeks older than its newest because listings get bumped and
   photos re-uploaded — ordering has nothing to do with photo date. The original
   age-floor rule therefore marked nearly every absent listing as "vanished" and
   spent 15 page fetches a run (~6 MB, ~3.7 GB/month — more than the €5 buys).
   Replaced with **persistence**: Vinted's search churns a few results every poll
   (187/190/188 for the same query), so a listing must be absent from **three
   consecutive polls** before earning a confirmation, capped at 6 per run.

### Safety properties worth keeping
- A run that cannot fetch the feed **aborts without touching state** — no
  corruption, next run recovers.
- An **incomplete** feed skips confirmations entirely; absence means nothing when
  half the window is missing.
- An unconfirmable listing returns `unknown`, stays tracked, and **never alerts**.
  The watcher under-reports rather than lying.
- Three consecutive failed confirmations abort the rest of the run's checks.

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
Phase 0 is **done and live** — read "STATUS" above, then `git -C /extra/malmasik/vinted log`
and the most recent `watch` run in Actions. Check whether any SOLD alert has fired.

## OPEN ITEMS
- **✅ DataImpulse password rotated 2026-07-27** (`~/.config/proxy.env` + `PROXY_URL`
  secret both updated, verified by a clean run). History, so it is not repeated:
  on 2026-07-27 ~14:49 a diagnostic ran
  `curl -v` through the proxy and printed `Proxy-Authorization: Basic <base64>`
  into an Actions log while the repo was briefly public. GitHub masks `PROXY_URL`
  but not its base64 encoding. The run log was deleted and the repo made private
  within ~2 min; the password is still the leaked one. After rotating, update
  `~/.config/proxy.env` **and** the `PROXY_URL` repo secret.
  **Never run `curl -v` through the proxy in CI.**
- ~~Rotate the password~~ **done 2026-07-27**; repo is **public** (unlimited free
  Actions minutes).

## SCHEDULING — use SLURM, not cron, not GitHub's `schedule:`
Two schedulers were tried and both failed on 2026-07-27:
- **GitHub `schedule:`** fired **once in 3.5 hours** where `*/20` should have fired
  ~10 times. Workflow `active`, cron valid, on the default branch, Actions enabled
  the whole time. Left in the workflow as a harmless backstop only.
- **User cron on labsrv7** installs fine (`crontab -l` correct, `cron.service`
  active) but **never executes** — zero heartbeats from a `* * * * *` job in 200s.
  Do not waste time on it again.

**What works: `scheduler/slurm_auto.sbatch`**, mirroring job-monitor's
self-perpetuating pattern — it re-arms itself (`sbatch --begin=now+20minutes`)
*before* doing anything, so a failure cannot break the chain. The cluster cannot
reach the proxy, so it does not scrape: it only calls `gh workflow run watch.yml`
(~2 s), and GitHub Actions does the scraping from an IP that can reach DataImpulse.

    START:  sbatch scheduler/slurm_auto.sbatch
    STOP:   scancel -n vinted-trigger
    WATCH:  squeue -n vinted-trigger ; tail -f data/logs/slurm_auto_*.out

Verified 2026-07-27 23:57 local: job ran, dispatched, and queued its successor.
- **✅ SOLD DETECTION VERIFIED against two real sales (2026-07-27 18:56Z).**
  `9505849905` (2000s Prada Sport suede shoes, €70) and `9493035670` (Zara shoes
  inspired by Prada, €18) both sold and were correctly reported to Telegram.
  The page structure, captured from the raw evidence dump:
  ```json
  {"name":"item_status","data":{"item_id":9505849905,"seller_id":32001697,
    "is_draft":false,"is_reserved":false,"is_hidden":false,
    "is_closed":true,"item_closing_action":"sold","transaction_permitted":true}}
  {"name":"buyer_item_status","data":{"item_id":9505849905,
    "title":"Venduto","theme":"SUCCESS"}}
  ```
  So a sale sets **`is_closed: true` AND `item_closing_action: "sold"`**, and the
  `buyer_item_status` block is what renders the on-screen "Venduto" badge.
  `check_sold()` now anchors on `"item_id":<id>` inside that block, so it cannot
  read a photo's flags or another listing's state.
- **⚠️ `is_hidden` must never drive a verdict.** It appears ~53x per item page
  because every photo carries one, and no anchoring window reliably separates the
  listing's flag from a photo's — reading it gave contradictory verdicts for the
  same listing an hour apart. Verdicts rest on `item_closing_action` and `is_closed`.
- **⚠️ The GitHub cron has never actually fired.** Every run so far is
  `workflow_dispatch`. Config is correct (default branch, workflow `active`,
  cron on `main`), so this is GitHub's scheduler being unreliable — it deprioritises
  and drops scheduled runs, especially on round-minute crons. Changed `*/20` to
  `7,27,47` to dodge the contended boundaries. **If it still does not fire, the
  watcher is not actually running** — check `gh run list --workflow=watch.yml`
  for `event=schedule` before assuming it works.
- Phases 2-4 (eBay sold-comps, SigLIP visual verify, arbitrage alerts) are untouched.
