#!/usr/bin/env bash
# Entry point on the rented machine. Whatever happens, the instance destroys itself at the end,
# and a watchdog destroys it at the hard deadline even if the job hangs.
set -uo pipefail
cd /opt/app
now=$(date +%s); left=$(( ${DEADLINE_EPOCH:-$((now + 900))} - now ))
( sleep "$(( left > 0 ? left : 1 ))"; python3 -m worker.selfdestruct "deadline" ) &
python3 -m worker.worker --remote "$@" 2>&1 | tee /tmp/worker.log   # "$@": e.g. --scenes
rc=${PIPESTATUS[0]}
curl -s -X PUT --upload-file /tmp/worker.log "${LOG_PUT_URL:-http://invalid}" >/dev/null || true
python3 -m worker.selfdestruct "finished rc=$rc"
exit "$rc"
