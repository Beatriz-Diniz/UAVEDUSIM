#!/usr/bin/env bash

set -e

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PX4_DIR="$SCRIPT_DIR/PX4-Autopilot"
CLEANUP_SCRIPT="$SCRIPT_DIR/cleanup_px4_gazebo.sh"

cleaned=0

cleanup() {
    if [[ "$cleaned" -eq 0 ]]; then
        cleaned=1
        "$CLEANUP_SCRIPT"
    fi
}

trap cleanup EXIT INT TERM

cd "$PX4_DIR"

"$@"
