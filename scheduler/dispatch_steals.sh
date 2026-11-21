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

# A run QUEUED far longer than any real queue wait is stuck, not waiting.
# On 2026-11-06 a dispatch returned an error yet GitHub created the run and
# never started it; it stayed "queued" (cancel and force-cancel both refused
# it), and because this check skipped whenever the newest run was queued, the
# steals tracker dispatched nothing for 15 days. So: a queued run older than
# STUCK_MIN is logged, cancelled if GitHub allows, and dispatching goes ahead.
# A run in progress is never touched - the workflow's own timeout ends it.
STUCK_MIN=${STUCK_MIN:-45}
read -r state created run_id <<< "$("$GH" run list --workflow="$WORKFLOW" --limit 1 \
        --json status,createdAt,databaseId -q '.[0] | "\(.status) \(.createdAt) \(.databaseId)"' 2>/dev/null)"
age_min=$(( ( $(date +%s) - $(date -d "${created:-now}" +%s) ) / 60 ))
if [[ ( "$state" == "queued" || "$state" == "pending" ) && $age_min -gt $STUCK_MIN ]]; then
  log "run $run_id $state for ${age_min}m - stuck; cancelling and dispatching anyway"
  "$GH" run cancel "$run_id" >/dev/null 2>&1 || true
elif [[ "$state" == "in_progress" || "$state" == "queued" || "$state" == "pending" ]]; then
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
