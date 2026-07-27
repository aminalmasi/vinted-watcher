#!/bin/bash
# Dispatch the vinted-watcher GitHub Actions workflow.
#
# GitHub's own `schedule:` trigger proved unreliable — on 2026-07-27 it fired
# once in 3.5 hours where it should have fired ~10 times, with the workflow
# `active` and the cron valid on the default branch throughout. This cron drives
# it explicitly. The workflow's `schedule:` block is deliberately left in place
# as a backstop; its concurrency group makes a doubled trigger queue rather than
# race the state file.
#
# Installed as: */20 * * * * /extra/malmasik/vinted/scheduler/trigger_watch.sh

export HOME=/home/malmasik
export PATH=/extra/malmasik/.local/bin:/usr/local/bin:/usr/bin:/bin

REPO=/extra/malmasik/vinted
LOG=$REPO/scheduler/trigger.log
STAMP=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

cd "$REPO" || { echo "$STAMP  FATAL cannot cd $REPO" >>"$LOG"; exit 1; }

if out=$(gh workflow run watch.yml 2>&1); then
  echo "$STAMP  dispatched" >>"$LOG"
else
  echo "$STAMP  FAILED: $out" >>"$LOG"
fi

# Keep the log from growing without bound (~2600 lines/month at 20-min cadence).
if [ "$(wc -l <"$LOG" 2>/dev/null || echo 0)" -gt 5000 ]; then
  tail -1000 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
