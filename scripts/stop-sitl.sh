#!/usr/bin/env bash
set -Eeuo pipefail

pid_file="${1:?PID file path is required}"
if [[ ! -f "$pid_file" ]]; then
  exit 0
fi

pid="$(tr -dc '0-9' < "$pid_file")"
if [[ -n "$pid" && -r "/proc/$pid/cmdline" ]]; then
  command_line="$(tr '\0' ' ' < "/proc/$pid/cmdline")"
  if [[ "$command_line" == *"start-sitl.sh"* ]]; then
    kill -TERM "$pid"
  fi
fi
rm -f "$pid_file"

