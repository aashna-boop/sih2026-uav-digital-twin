#!/usr/bin/env bash
set -Eeuo pipefail

ardupilot_home="${ARDUPILOT_HOME:-$HOME/ardupilot}"
pid_file="${AEGISTWIN_SITL_PID_FILE:-/tmp/aegistwin-sitl.pid}"

sim_vehicle="$ardupilot_home/Tools/autotest/sim_vehicle.py"
if [[ ! -x "$sim_vehicle" ]]; then
  echo "ArduPilot SITL launcher not found at $sim_vehicle" >&2
  exit 2
fi

cleanup() {
  if [[ -n "${sitl_group_pid:-}" ]]; then
    kill -TERM -- "-$sitl_group_pid" 2>/dev/null || true
    wait "$sitl_group_pid" 2>/dev/null || true
  fi
  rm -f "$pid_file"
}
trap cleanup EXIT INT TERM

echo "Starting ArduPlane SITL with dedicated AegisTwin TCP channel on port 5770"
echo "$$" > "$pid_file"

setsid python3 "$sim_vehicle" \
  -v ArduPlane \
  -w \
  --no-rebuild \
  --speedup 1 \
  --no-mavproxy \
  -A "--serial0=tcp:5770:wait" &
sitl_group_pid=$!
wait "$sitl_group_pid"
