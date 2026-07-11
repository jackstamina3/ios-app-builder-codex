#!/usr/bin/env bash
set -euo pipefail

: "${TARGET_JSON:?TARGET_JSON is required}"
: "${SOURCE_DIR:?SOURCE_DIR is required}"
: "${BUILDER_DIR:?BUILDER_DIR is required}"
: "${ADAPTER_ENV_FILE:?ADAPTER_ENV_FILE is required}"

kind="$(jq -r '.bootstrap.kind' "$TARGET_JSON")"
working="$(jq -r '.working_directory' "$TARGET_JSON")"
workdir="$(python3 - "$SOURCE_DIR" "$working" <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
candidate = (root / sys.argv[2]).resolve(strict=True)
try:
    candidate.relative_to(root)
except ValueError:
    raise SystemExit("working directory escapes source root")
if not candidate.is_dir():
    raise SystemExit("working directory is not a directory")
print(candidate)
PY
)"
container_type="$(jq -r '.container.type' "$TARGET_JSON")"
container_path="$(jq -r '.container.path' "$TARGET_JSON")"
scheme="$(jq -r '.scheme' "$TARGET_JSON")"
container_flag="-$container_type"
case "$kind" in
  none) : >"$ADAPTER_ENV_FILE" ;;
  swiftpm)
    : >"$ADAPTER_ENV_FILE"
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
    xcodebuild "$container_flag" "$container" -scheme "$scheme" -resolvePackageDependencies
    ;;
  cocoapods)
    : >"$ADAPTER_ENV_FILE"
    if [[ -f "$workdir/Gemfile" ]]; then
      (cd "$workdir" && bundle config set path "$HOME/.bundle" && bundle exec pod install)
    else
      (cd "$workdir" && pod install)
    fi
    ;;
  carthage)
    : >"$ADAPTER_ENV_FILE"
    (cd "$workdir" && carthage bootstrap --use-xcframeworks)
    ;;
  adapter)
    adapter="$(jq -r '.bootstrap.adapter' "$TARGET_JSON")"
    "$BUILDER_DIR/$adapter"
    ;;
  *) echo "Unsupported bootstrap kind: $kind" >&2; exit 1 ;;
esac
