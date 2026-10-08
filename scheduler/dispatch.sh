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
LAST=/extra/malmasik/vinted_sched/last_dispatch

# Minimum minutes between cycles.
#
# Vinted's budget is SHARED, not per-IP: in every blocked cycle all five or six
# shards are refused in the same minutes on unrelated addresses across
# different /8s, while in a clean cycle all of them succeed. What those jobs
# share is the time window and the Azure ASN every GitHub runner sits in - so
# sharding never bought independent allowances, it just divided one.
#
# Measured share of cycles hitting a 403, by the interval they ran at:
#
#   T=1.53h   7%   <- cleanest observed, this setting
#   T=1.33h  17%
#   T=1.26h  55%
#   T=1.06h  91%
#   T=0.98h  83%
#
# It degrades sharply below ~1.3h, consistent with a shared bucket that needs
# time to refill. 7% is the best measured, not zero - roughly one cycle in
# fourteen still gets refused, and the backoff handles that cheaply.
#
# Note the volume figures quoted earlier were unreliable: they assumed a
# constant ~1,700 requests per cycle, which was not true across days when
# per-cycle bursts differed. The interval and the observed 403 rate are the
# measurements worth pacing on.
MIN_GAP_MIN=${VT_MIN_GAP_MIN:-92}

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

if [[ -f "$LAST" ]]; then
  since=$(( ( $(date +%s) - $(stat -c %Y "$LAST") ) / 60 ))
  if (( since < MIN_GAP_MIN )); then
    log "skip - only ${since}m since last dispatch (min ${MIN_GAP_MIN}m)"
    exit 0
  fi
fi

if "$GH" workflow run "$WORKFLOW" >/dev/null 2>&1; then
  touch "$LAST"
  log "dispatched"
else
  log "dispatch FAILED"
  exit 1
fi
