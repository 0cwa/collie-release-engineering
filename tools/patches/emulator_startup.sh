#!/usr/bin/env bash
# Shared by the release instrumentation workflows.

check_avd_disk_space() {
  # 9 GiB leaves more than 2 GB above the observed 7,373 MB requirement.
  local required_kib=9437184 available_kib
  available_kib=$(df -Pk "$ANDROID_AVD_HOME" | awk 'NR == 2 {print $4}')
  if [[ ! "$available_kib" =~ ^[0-9]+$ ]]; then
    echo "Could not determine free disk space for ANDROID_AVD_HOME=$ANDROID_AVD_HOME" >&2
    return 1
  fi
  if (( available_kib < required_kib )); then
    echo "Insufficient disk space for Android emulator at $ANDROID_AVD_HOME: ${available_kib} KiB available; ${required_kib} KiB required (9 GiB)." >&2
    return 1
  fi
  echo "Android emulator disk space at $ANDROID_AVD_HOME: ${available_kib} KiB available; ${required_kib} KiB required."
}

wait_for_emulator_boot() {
  local emulator_pid=$1 deadline=$((SECONDS + 300)) booted
  while (( SECONDS < deadline )); do
    if ! kill -0 "$emulator_pid" 2>/dev/null; then
      echo "Android emulator process $emulator_pid exited before boot completed." >&2
      return 1
    fi
    booted=$(timeout 5 adb shell getprop sys.boot_completed | tr -d '\r') || true
    if [[ "$booted" == 1 ]]; then
      return 0
    fi
    sleep 2
  done
  echo "Android emulator did not finish booting within 300 seconds." >&2
  return 1
}
