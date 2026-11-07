#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/emulator_startup.sh"
export ANDROID_AVD_HOME='/example/avd home'

df() {
  [[ "$1" == -Pk && "$2" == "$ANDROID_AVD_HOME" ]] || return 1
  printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\nfixture 10000000 1 %s 1%% /\n' "$fixture_available"
}
fixture_available=9437184
check_avd_disk_space >/dev/null
fixture_available=9437183
if output=$(check_avd_disk_space 2>&1); then
  echo 'Disk check accepted insufficient space' >&2; exit 1
fi
[[ "$output" == *'Insufficient disk space'* && "$output" == *"$ANDROID_AVD_HOME"* ]] || exit 1
fixture_available=invalid
if output=$(check_avd_disk_space 2>&1); then
  echo 'Disk check accepted invalid df output' >&2; exit 1
fi
[[ "$output" == *'Could not determine free disk space'* ]] || exit 1

adb() { printf '1\r\n'; }
timeout() { shift; "$@"; }
sleep 30 &
pid=$!
trap 'kill "$pid" 2>/dev/null || true' EXIT
wait_for_emulator_boot "$pid"
kill "$pid"
wait "$pid" 2>/dev/null || true
if output=$(wait_for_emulator_boot "$pid" 2>&1); then
  echo 'Boot check accepted an exited emulator' >&2; exit 1
fi
[[ "$output" == *'exited before boot completed'* ]] || exit 1
echo 'Emulator startup checks passed.'
