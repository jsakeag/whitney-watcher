#!/usr/bin/env bash
# Keeps the Mac awake (caffeinate) while polling recreation.gov for permit
# availability. Usage matches whitney_watch.py's args, e.g.:
#
#   ./run_watch.sh 2026-11-05
#   ./run_watch.sh 2026-11-05 --interval 180 --stop-on-found
#
# -d  prevent display sleep
# -i  prevent idle sleep
# -s  prevent system sleep (on AC power)

set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 YYYY-MM-DD [extra whitney_watch.py args...]"
    exit 1
fi

caffeinate -dis python3 -u "$DIR/whitney_watch.py" "$@"