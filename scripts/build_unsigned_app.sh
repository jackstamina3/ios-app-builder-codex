#!/usr/bin/env bash
set -euo pipefail

: "${TARGET_JSON:?TARGET_JSON is required}"
: "${SOURCE_DIR:?SOURCE_DIR is required}"
: "${BUILD_DIR:?BUILD_DIR is required}"
: "${OUTPUT_DIR:?OUTPUT_DIR is required}"
: "${BUILDER_DIR:?BUILDER_DIR is required}"
: "${DEVELOPER_DIR:?DEVELOPER_DIR is required}"
: "${SAFE_HOME:?SAFE_HOME is required}"

container_type="$(jq -r '.container.type' "$TARGET_JSON")"
container_path="$(jq -r '.container.path' "$TARGET_JSON")"
scheme="$(jq -r '.scheme' "$TARGET_JSON")"
configuration="$(jq -r '.configuration' "$TARGET_JSON")"
action="$(jq -r '.build_action' "$TARGET_JSON")"
working="$(jq -r '.working_directory' "$TARGET_JSON")"
expected_app="$(jq -r '.output.expected_app_bundle' "$TARGET_JSON")"

adapter_env="$BUILD_DIR/adapter.env"
mkdir -p "$BUILD_DIR" "$OUTPUT_DIR" "$SAFE_HOME"
export ADAPTER_ENV_FILE="$adapter_env"
"$BUILDER_DIR/scripts/bootstrap_dependencies.sh"

container="$(python3 - "$SOURCE_DIR" "$working/$container_path" <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
candidate = (root / sys.argv[2]).resolve(strict=True)
try:
    candidate.relative_to(root)
except ValueError:
    raise SystemExit("container escapes source root")
if not candidate.is_dir():
    raise SystemExit("container is not a directory")
print(candidate)
PY
)"

declare -a clean_env=(
  "PATH=$PATH" "HOME=$SAFE_HOME" "TMPDIR=${TMPDIR:-$BUILD_DIR/tmp}"
  "USER=runner" "LOGNAME=runner" "SHELL=/bin/bash" "LANG=en_US.UTF-8"
  "LC_ALL=en_US.UTF-8" "CI=1" "DEVELOPER_DIR=$DEVELOPER_DIR"
)
while IFS= read -r entry; do clean_env+=("$entry"); done < <(jq -r '.build_environment // {} | to_entries[] | "\(.key)=\(.value)"' "$TARGET_JSON")
while IFS='=' read -r key value; do
  [[ -z "$key" ]] && continue
  case "$key" in JAVA_HOME|GRADLE_USER_HOME|GRADLE_OPTS|KOTLIN_DAEMON_JVMARGS|ORG_GRADLE_PROJECT_org.gradle.jvmargs|ORG_GRADLE_PROJECT_kotlin.daemon.jvmargs|ORG_GRADLE_PROJECT_kotlin.native.jvmArgs) clean_env+=("$key=$value");; *) echo "Adapter emitted forbidden variable: $key" >&2; exit 1;; esac
done <"$adapter_env"

container_flag="-$container_type"
list_json="$BUILD_DIR/xcode-list.json"
env -i "${clean_env[@]}" xcodebuild "$container_flag" "$container" -list -json >"$list_json"
jq -e --arg scheme "$scheme" '[.. | .schemes? // empty | .[]] | index($scheme) != null' "$list_json" >/dev/null || {
  echo "Scheme $scheme is unavailable" >&2; jq '.. | .schemes? // empty' "$list_json" >&2; exit 1;
}

result_dir="${DIAGNOSTICS_DIR:-$BUILD_DIR}"
mkdir -p "$result_dir"
declare -a args=("$container_flag" "$container" -scheme "$scheme" -configuration "$configuration" -sdk iphoneos -destination 'generic/platform=iOS' -derivedDataPath "$BUILD_DIR/DerivedData" -resultBundlePath "$result_dir/Build.xcresult" -showBuildTimingSummary)
if [[ "$action" == archive ]]; then
  args+=(-archivePath "$BUILD_DIR/App.xcarchive" clean archive)
else
  args+=(clean build)
fi
while IFS= read -r setting; do args+=("$setting"); done < <(jq -r '.extra_build_settings // {} | to_entries[] | "\(.key)=\(.value)"' "$TARGET_JSON")
args+=(CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY= EXPANDED_CODE_SIGN_IDENTITY= DEVELOPMENT_TEAM= PROVISIONING_PROFILE= PROVISIONING_PROFILE_SPECIFIER= COMPILER_INDEX_STORE_ENABLE=NO)

set -o pipefail
env -i "${clean_env[@]}" xcodebuild "${args[@]}" 2>&1 | tee "$result_dir/xcodebuild.log"

if [[ "$action" == archive ]]; then
  app_root="$BUILD_DIR/App.xcarchive/Products/Applications"
else
  app_root="$BUILD_DIR/DerivedData/Build/Products/${configuration}-iphoneos"
fi
mapfile_path="$BUILD_DIR/app-candidates.txt"
find "$app_root" -maxdepth 1 -type d -name "$expected_app" -print >"$mapfile_path"
app_count="$(wc -l <"$mapfile_path" | tr -d ' ')"
[[ "$app_count" == 1 ]] || { echo "Expected exactly one $expected_app, found $app_count" >&2; exit 1; }
app_path="$(sed -n '1p' "$mapfile_path")"
printf '%s\n' "$app_path" >"$BUILD_DIR/app-path.txt"
