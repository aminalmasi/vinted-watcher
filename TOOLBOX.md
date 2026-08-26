# vc — the toolbox

    vc <command> [args]

Available anywhere (alias in `~/.bashrc` → `/extra/malmasik/vinted/vc`).

## Why some commands are slow

The work is split across two machines and there is no way around it:

* **Vestiaire's search host 403s the university IP** — it only answers GitHub
  Actions. Their `apiv2/products/{id}` and `/brands` do answer the cluster.
* **Vinted blocks datacenter IPs**, so Vinted must be read from the cluster.
  It needs no proxy there: loading the homepage sets an anonymous token.

So anything touching Vestiaire *search* is dispatched to Actions and waited on
(2–20 minutes). Anything local is instant. `vc` hides the difference.

| command | where | time |
|---|---|---|
| `vc status` | local | instant |
| `vc likes` | local | instant |
| `vc compare <url>` | both | ~3 min |
| `vc top` / `vc view` | Actions | ~15 min |
| `vc hot` | Actions | ~12 min |
| `vc brands` | Actions | ~3 min |
| `vc digest` | Actions | ~12 min |

## Commands

**`vc status`** — recent sweeps, listings tracked, sales detected in 24h/7d,
last digest, and a warning if the watchdog has seen blind sweeps.

**`vc compare <vinted-url> [k]`** — prices a Vinted listing against Vestiaire.
Reads the listing on the cluster, maps brand/condition/subcategory to
Vestiaire's ids, then reports median and IQR for two arms: what **sold** and
what is **live**. Tier A matching only — brand + subcategory + condition. No
colour, no size, and never the model name as a *filter*, because half of
Vinted's titles are "Sandals". The name only orders results.

**`vc likes`** — reads `data/vc_cohort.jsonl` and asks whether likes predict a
sale. Offline. Refuses to show a table under 20 confirmed sales.

**`vc top [floor_cents] [n]`** — HTML grid of the most-liked live listings above
a price floor. **`vc view`** is the same data one item per screen with 900px
photos and arrow-key navigation. Saved to `~/vinted_reports/`.

**`vc hot`** — live listings ranked by likes gained per day.

**`vc brands`** — every brand on the site ranked by shoes sold above €150.

**`vc digest`** — sends today's Telegram digest immediately.

## Things that will mislead you if forgotten

* **`totalHits` saturates at 10,000.** Two capped numbers compared to each other
  prove nothing. This produced two wrong conclusions during development.
* **Pagination dies around offset 1,000.** Wide queries are samples, not
  censuses; the tools say which they are.
* **Sold price ≠ sale price.** Vestiaire shows the listing price on sold items
  and keeps accepted offers private. Treat it as an upper bound.
* **Never rank comparables by price.** Ranking on price closeness and then
  taking a median hands your input back as if it were an answer.
* **Vinted's item detail API (`/api/v2/items/{id}`) is a permanent 404.**
  Attributes come from the item HTML page.
* **The rate limit is ~1 request/2s.** Everything paces at 6–10s; a 429 means
  back off, not retry.
