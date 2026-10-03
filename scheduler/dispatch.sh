#!/usr/bin/env bash
# Trigger the Vinted tracker from the cluster, because GitHub's own scheduler
# will not.
#
# Measured: a '*/30' cron asks for 48 runs a day and GitHub creates 4-6. None
# are cancelled - they are simply never created. 24h / 6 = 4.0h, which is
# exactly the sweep interval we were stuck at. Raising the cron frequency does
# nothing; scheduled workflows are throttled regardless of what is requested.
#
# Self-chaining inside Actions is also out: GitHub blocks runs triggered by
# GITHUB_TOKEN from starting further runs, so it would need a personal access
# token. A workflow_dispatch from here needs no new credential - gh is already
# authenticated on this machine - and costs one API call.
#
# This sends a trigger and nothing else. No listing pages are fetched from the
# university IP.
set -uo pipefail

GH=/extra/malmasik/.local/bin/gh
REPO_DIR=/extra/malmasik/vinted
WORKFLOW=vinted-track-split.yml
# Outside the repo deliberately: anything inside it risks being swept
# into a commit, which is how a stale state file once reverted 37 sales.
LOG=/extra/malmasik/vinted_sched/dispatch.log
STOP=/extra/malmasik/vinted_sched/STOP_DISPATCH

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >> "$LOG"; }

# Kill switch: create the STOP file and this stops triggering, no edits needed.
if [[ -f "$STOP" ]]; then
  log "STOP_DISPATCH present - not triggering"
  exit 0
fi

cd "$REPO_DIR" || { log "cannot cd $REPO_DIR"; exit 1; }

# Don't pile up. The workflow has a concurrency group so an extra run would
# only queue, but queued runs get replaced rather than accumulated, and
# triggering while one is mid-flight just churns the queue.
state=$("$GH" run list --workflow="$WORKFLOW" --limit 1 \
        --json status -q '.[0].status' 2>/dev/null)
if [[ "$state" == "in_progress" || "$state" == "queued" || "$state" == "pending" ]]; then
  log "skip - a run is already $state"
  exit 0
fi

if "$GH" workflow run "$WORKFLOW" >/dev/null 2>&1; then
  log "dispatched"
else
  log "dispatch FAILED"
  exit 1
fi
