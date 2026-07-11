#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 APP_BUNDLE OUTPUT.unsigned.ipa" >&2
  exit 64
}

if [[ $# -eq 0 ]]; then
  : "${BUILD_DIR:?BUILD_DIR is required in environment mode}"
  : "${OUTPUT_DIR:?OUTPUT_DIR is required in environment mode}"
  : "${TARGET_JSON:?TARGET_JSON is required in environment mode}"
  readarray_file=$(mktemp "${TMPDIR:-/tmp}/ipa-apps.XXXXXX")
  python3 - "$TARGET_JSON" > "$readarray_file" <<'PY'
import json, sys
target = json.load(open(sys.argv[1]))
print(target["output"]["expected_app_bundle"])
print(target["source"]["commit"][:7])
PY
  expected=$(sed -n '1p' "$readarray_file")
  short_commit=$(sed -n '2p' "$readarray_file")
  rm -f "$readarray_file"
  candidates=$(mktemp "${TMPDIR:-/tmp}/ipa-apps.XXXXXX")
  find "$BUILD_DIR" -type d -name "$expected" -path '*/Products/Applications/*' -print > "$candidates"
  count=$(wc -l < "$candidates" | tr -d ' ')
  [[ "$count" == 1 ]] || { echo "expected exactly one archived $expected under $BUILD_DIR, found $count" >&2; rm -f "$candidates"; exit 65; }
  app=$(sed -n '1p' "$candidates")
  rm -f "$candidates"
  version_fields=$(python3 - "$app/Info.plist" <<'PY'
import plistlib, sys
with open(sys.argv[1], "rb") as handle:
    value = plistlib.load(handle)
for key in ("CFBundleShortVersionString", "CFBundleVersion"):
    field = str(value.get(key, "unknown"))
    if not field or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for c in field):
        raise SystemExit(f"unsafe or missing {key}")
    print(field)
PY
  )
  short_version=$(printf '%s\n' "$version_fields" | sed -n '1p')
  bundle_version=$(printf '%s\n' "$version_fields" | sed -n '2p')
  output="$OUTPUT_DIR/Nuvio-${short_version}-${bundle_version}-${short_commit}.unsigned.ipa"
elif [[ $# -eq 2 ]]; then
  app=$1
  output=$2
else
  usage
fi
[[ -d "$app" && "$app" == *.app ]] || { echo "not an application bundle: $app" >&2; exit 65; }
[[ "$output" == *.unsigned.ipa ]] || { echo "output must end in .unsigned.ipa" >&2; exit 65; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 69; }

case "$output" in
  /*) ;;
  *) output="$PWD/$output" ;;
esac
mkdir -p "$(dirname "$output")"
stage=$(mktemp -d "${TMPDIR:-/tmp}/ipa-package.XXXXXX")
trap 'rm -rf "$stage"' EXIT
mkdir "$stage/Payload"

# ditto preserves macOS bundle metadata; cp is adequate for portable test hosts.
if command -v ditto >/dev/null; then
  ditto "$app" "$stage/Payload/$(basename "$app")"
else
  cp -R "$app" "$stage/Payload/$(basename "$app")"
fi

python3 - "$stage" "$output" <<'PY'
import os
import stat
import sys
import zipfile

root, output = sys.argv[1:]
temporary = output + ".tmp"
try:
    os.unlink(temporary)
except FileNotFoundError:
    pass
with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        relative_dir = os.path.relpath(directory, root)
        for name in dirnames:
            path = os.path.join(directory, name)
            if os.path.islink(path):
                relative = os.path.normpath(os.path.join(relative_dir, name)).replace(os.sep, "/")
                raise SystemExit(f"refusing symlink in application bundle: {relative}")
        for name in filenames:
            path = os.path.join(directory, name)
            relative = os.path.normpath(os.path.join(relative_dir, name)).replace(os.sep, "/")
            if os.path.islink(path):
                raise SystemExit(f"refusing symlink in application bundle: {relative}")
            info = zipfile.ZipInfo.from_file(path, relative)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(path, "rb") as handle:
                archive.writestr(info, handle.read(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
os.replace(temporary, output)
PY

if [[ $# -eq 0 ]]; then
  (cd "$OUTPUT_DIR" && shasum -a 256 "$(basename "$output")") > "$OUTPUT_DIR/SHA256SUMS"
  if [[ -n "${SOURCE_DIR:-}" ]]; then
    license_path=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source"]["license_file"])' "$TARGET_JSON")
    [[ -f "$SOURCE_DIR/$license_path" ]] || { echo "license file missing: $license_path" >&2; exit 65; }
    cp "$SOURCE_DIR/$license_path" "$OUTPUT_DIR/LICENSE"
    python3 - "$TARGET_JSON" "$SOURCE_DIR" "$OUTPUT_DIR/source-metadata.json" <<'PY'
import json, subprocess, sys
target_path, source_dir, output = sys.argv[1:]
target = json.load(open(target_path))
submodules = []
status = subprocess.run(["git", "-C", source_dir, "submodule", "status", "--recursive"], capture_output=True, text=True)
if status.returncode == 0:
    for line in status.stdout.splitlines():
        fields = line.lstrip(" +-U").split()
        if len(fields) >= 2:
            submodules.append({"commit": fields[0], "path": fields[1]})
source = dict(target["source"])
source["url"] = "https://github.com/" + source["repository"]
with open(output, "w", encoding="utf-8") as handle:
    json.dump({"format_version": 1, "source": source, "submodules": submodules}, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
  fi
fi

echo "$output"
