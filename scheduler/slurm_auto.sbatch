#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Self-perpetuating trigger for the vinted-watcher GitHub Actions workflow.
#
# Why SLURM and not cron: user crontabs are installed fine on labsrv7 but never
# execute (verified 2026-07-27 — cron.service active, crontab -l correct, zero
# heartbeats in 200s). And GitHub's own `schedule:` trigger fired once in 3.5
# hours when it should have fired ~10 times. SLURM is the one scheduler on this
# cluster proven to work, and job-monitor already relies on the same pattern.
#
# The cluster cannot reach the DataImpulse proxy, so it does NOT scrape Vinted;
# it only tells GitHub Actions to run, which does the scraping from an IP that
# can reach the proxy. That is a ~2 second `gh` call.
#
# START:  sbatch scheduler/slurm_auto.sbatch
# STOP :  scancel -n vinted-trigger
# WATCH:  squeue -n vinted-trigger ; tail -f data/logs/slurm_auto_*.out
# ─────────────────────────────────────────────────────────────────────────────
#SBATCH -J vinted-trigger
#SBATCH -p allgroups
#SBATCH -c 1
#SBATCH --mem=512M
#SBATCH --time=00:05:00
#SBATCH -o /extra/malmasik/vinted/data/logs/slurm_auto_%j.out

PROJECT=/extra/malmasik/vinted
export HOME=/home/malmasik
export PATH=/extra/malmasik/.local/bin:/usr/local/bin:/usr/bin:/bin
cd "$PROJECT" || exit 1

# 1) Re-arm the next cycle FIRST, so a failure below cannot break the chain.
sbatch --begin=now+20minutes "$PROJECT/scheduler/slurm_auto.sbatch"

# 2) Ask GitHub Actions to run one poll.
echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') dispatching watch.yml"
gh workflow run watch.yml || echo "dispatch failed (chain continues)"
