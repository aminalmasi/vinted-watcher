#!/usr/bin/env bash
# Trigger the steals tracker (vinted-steals.yml) from the cluster every ~30
# minutes. Like dispatch.sh, this sends a trigger and nothing else - no
# listing pages are fetched from the university IP.
#
# Kill switch: touch /extra/malmasik/vinted_sched/STOP_STEALS
set -uo pipefail

GH=/extra/malmasik/.local/bin/gh
REPO_DIR=/extra/malmasik/vinted
WORKFLOW=vinted-steals.yml
LOG=/extra/malmasik/vinted_sched/steals.log
STOP=/extra/malmasik/vinted_sched/STOP_STEALS
LAST=/extra/malmasik/vinted_sched/last_steals
MIN_GAP_MIN=${VS_MIN_GAP_MIN:-25}

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >> "$LOG"; }

[[ -f "$STOP" ]] && { log "STOP_STEALS present - not triggering"; exit 0; }
cd "$REPO_DIR" || { log "cannot cd $REPO_DIR"; exit 1; }

state=$("$GH" run list --workflow="$WORKFLOW" --limit 1 \
        --json status -q '.[0].status' 2>/dev/null)
if [[ "$state" == "in_progress" || "$state" == "queued" || "$state" == "pending" ]]; then
  log "skip - a run is already $state"
  exit 0
fi

if [[ -f "$LAST" ]]; then
  since=$(( ( $(date +%s) - $(stat -c %Y "$LAST") ) / 60 ))
  (( since < MIN_GAP_MIN )) && { log "skip - only ${since}m since last"; exit 0; }
fi

if "$GH" workflow run "$WORKFLOW" >/dev/null 2>&1; then
  touch "$LAST"; log "dispatched"
else
  log "dispatch FAILED"; exit 1
fi
