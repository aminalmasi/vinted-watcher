# Cloudflare Worker — 20-minute trigger

Replaces the cluster as the clock. The Worker does **no scraping**: it POSTs to
the GitHub API so Actions runs one poll. Everything else is unchanged.

## Why not the alternatives (measured 2026-07-27)

| Scheduler | Outcome |
| --- | --- |
| GitHub `schedule:` | Fired **once in 3.5 h** where `*/20` needs ~10. Config was valid throughout. |
| User cron on `labsrv7` | Installs (`crontab -l` correct, `cron.service` active) but **never executes** — 0 heartbeats in 200 s. |
| SLURM chain | Worked, but ~2,200 tiny jobs/month on research HPC for a personal bot. Dropped deliberately. |

## Setup (~10 minutes, free)

### 1. GitHub token
Create a **fine-grained** PAT: <https://github.com/settings/personal-access-tokens/new>

- **Repository access** → Only select repositories → `vinted-watcher`
- **Permissions** → Repository permissions → **Actions: Read and write**
- Expiry: set a reminder — the trigger silently stops when it lapses.

Nothing else. It cannot touch any other repo.

### 2. Deploy

```bash
npm install -g wrangler        # once
cd scheduler/cloudflare
wrangler login                 # opens a browser
wrangler secret put GITHUB_TOKEN   # paste the PAT
wrangler secret put TRIGGER_KEY    # any random string, e.g. openssl rand -hex 16
wrangler deploy
```

`wrangler deploy` prints the Worker URL and confirms the cron trigger.

### 3. Verify it works

```bash
# manual fire — expect {"ok":true,"status":204}
curl "https://vinted-watcher-cron.<your-subdomain>.workers.dev/?key=<TRIGGER_KEY>"

# then confirm GitHub received it
gh run list --workflow=watch.yml --limit 3
```

Wait one 20-minute boundary and check again — a new run with event
`workflow_dispatch` means the cron is live.

```bash
wrangler tail        # live logs, shows "dispatched watch.yml"
```

## Deployed and verified (2026-07-28)

Worker: `https://vinted-watcher-cron.aminalmasi1998.workers.dev`
- Manual fire with the key returned `{"ok":true,"status":204}`.
- The `*/20` cron tick at 12:00:00Z produced a GitHub run at **12:00:19Z** —
  19 seconds late, against GitHub's own scheduler which was firing 2-3 *hours*
  apart on the same workflow.

## Changing the cadence

The cron lives in the Cloudflare dashboard (**Settings → Triggers → Cron
Triggers**), so `wrangler.toml` alone does not change a deployed Worker unless
you redeploy. Current setting: **`0 * * * *`** (hourly).

It was `*/20` until 2026-07-28, when bursty polling got our proxy exits blocked
from `vinted.it`. The watcher now also refuses to poll within 50 minutes of its
last run, so a faster trigger only produces skipped runs — not faster detection.

## Cost

Free plan: 100,000 requests/day and cron triggers included. This uses **72
requests/day**.

## If it stops firing

1. `wrangler tail` — is `scheduled` running at all?
2. A `401`/`403` in the logs means the PAT expired or lost `Actions: write`.
3. GitHub's own `schedule:` block is still in `watch.yml` as a weak backstop, so
   polling degrades rather than stops dead.
