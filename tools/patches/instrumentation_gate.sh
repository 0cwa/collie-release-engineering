#!/usr/bin/env bash
# Run on a disposable emulator with the selected reconstructed profile installed
# by Gradle. IMO uses a synthetic trust root and its APK must not be shipped.
set -euo pipefail
source_tree=$1
report_dir=$2
profile=$3
case "$profile" in
  collie|collie-foss) ;;
  *) echo "Unsupported instrumentation profile: $profile" >&2; exit 1 ;;
esac
# CI_PACKAGE_ID takes precedence over the selected profile in Gradle.
if [[ -n "${CI_PACKAGE_ID:-}" && "$CI_PACKAGE_ID" != im.collie.app ]]; then
  echo "CI_PACKAGE_ID conflicts with the selected instrumentation profile" >&2
  exit 1
fi
# Instrumentation uses the debug keystore, never release signing credentials.
unset CI_KEYSTORE_PATH CI_KEYSTORE_PASSWORD CI_KEYSTORE_ALIAS
mkdir -p "$report_dir"
results="$source_tree/app/build/outputs/androidTest-results/connected"
runner_check="$(dirname "$0")/instrumentation_results.py"
runner_check=$(realpath "$runner_check")
run_suite() {
  local suite=$1 runner=$2 package=$3
  shift 3
  # Remove old outputs so metadata cannot describe a previous suite or profile.
  rm -rf -- "$results" "$report_dir/$suite-results" "$source_tree/app/build/outputs/apk/prodWebsite/debug" \
    "$source_tree/app/build/outputs/apk/androidTest/prodWebsite/debug" || {
    echo "Failed to clean $suite instrumentation outputs" >&2
    return 1
  }
  local status=0 component
  python3 "$(dirname "$runner_check")/build.py" "$@" --worktree "$source_tree" \
    --report-dir "$report_dir" --profile "$profile" > "$report_dir/$suite.log" 2>&1 || status=$?
  cp -a -- "$results" "$report_dir/$suite-results" || {
    echo "Failed to copy $suite instrumentation results" >&2
    return 1
  }
  component=$(python3 "$(dirname "$runner_check")/instrumentation_install.py" "$source_tree" "$suite" "$runner") || return 1
  local -a filter=()
  if [[ "$suite" == default ]]; then
    filter=(--not-package "$package" --all-coverage-output "$report_dir/all-tests.json")
  else
    filter=(--package "$package")
  fi
  python3 "$runner_check" "$report_dir/$suite-results" "$component" "${filter[@]}" \
    --coverage-output "$report_dir/$suite-tests.json" || return 1
  if (( status != 0 )); then
    echo "$suite Gradle task failed with exit $status" >&2
    return 1
  fi
}
failed=0
run_suite default org.thoughtcrime.securesms.testing.SignalTestRunner org.thoughtcrime.securesms.messages.incomingmessageobserver instrumentation || exit 1
run_suite imo org.thoughtcrime.securesms.testing.incomingmessageobserver.IncomingMessageObserverTestRunner org.thoughtcrime.securesms.messages.incomingmessageobserver \
  instrumentation-imo || failed=1
if (( failed == 0 )); then
  python3 "$runner_check" --compare "$report_dir/all-tests.json" \
    "$report_dir/default-tests.json" "$report_dir/imo-tests.json" || failed=1
fi
exit "$failed"
