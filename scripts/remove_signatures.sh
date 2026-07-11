#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 APP_BUNDLE METADATA_JSON" >&2
  exit 64
}

if [[ $# -eq 0 ]]; then
  : "${BUILD_DIR:?BUILD_DIR is required in environment mode}"
  : "${OUTPUT_DIR:?OUTPUT_DIR is required in environment mode}"
  : "${TARGET_JSON:?TARGET_JSON is required in environment mode}"
  expected=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["output"]["expected_app_bundle"])' "$TARGET_JSON")
  mapfile_file=$(mktemp "${TMPDIR:-/tmp}/ipa-apps.XXXXXX")
  find "$BUILD_DIR" -type d -name "$expected" -path '*/Products/Applications/*' -print > "$mapfile_file"
  count=$(wc -l < "$mapfile_file" | tr -d ' ')
  [[ "$count" == 1 ]] || { echo "expected exactly one archived $expected under $BUILD_DIR, found $count" >&2; rm -f "$mapfile_file"; exit 65; }
  app=$(sed -n '1p' "$mapfile_file")
  rm -f "$mapfile_file"
  metadata="$OUTPUT_DIR/signing-metadata.json"
elif [[ $# -eq 2 ]]; then
  app=$1
  metadata=$2
else
  usage
fi

[[ -d "$app" && "$app" == *.app ]] || { echo "not an application bundle: $app" >&2; exit 65; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 69; }
command -v codesign >/dev/null || { echo "codesign is required (run on macOS)" >&2; exit 69; }

case "$metadata" in
  /*) ;;
  *) metadata="$PWD/$metadata" ;;
esac
mkdir -p "$(dirname "$metadata")"
work=$(mktemp -d "${TMPDIR:-/tmp}/ipa-signatures.XXXXXX")
trap 'rm -rf "$work"' EXIT
records="$work/records"
mkdir -p "$records"

# Capture bundle metadata and entitlements before changing the code objects.
index=0
while IFS= read -r -d '' bundle; do
  index=$((index + 1))
  rel=${bundle#"$app"/}
  [[ "$bundle" == "$app" ]] && rel="."
  printf '%s\0' "$rel" > "$records/$index.path"
  plist="$bundle/Info.plist"
  if [[ -f "$plist" ]]; then
    cp "$plist" "$records/$index.plist"
  fi
  if codesign -d --entitlements :- "$bundle" >"$records/$index.entitlements" 2>"$records/$index.codesign"; then
    :
  else
    rm -f "$records/$index.entitlements"
  fi
done < <(find "$app" -depth -type d \( -name '*.app' -o -name '*.appex' -o -name '*.framework' -o -name '*.xpc' \) -print0)

python3 - "$records" "$metadata" <<'PY'
import base64
import json
import os
import plistlib
import sys

records, destination = sys.argv[1:]
items = []
for name in sorted((n[:-5] for n in os.listdir(records) if n.endswith(".path")), key=int):
    prefix = os.path.join(records, name)
    with open(prefix + ".path", "rb") as handle:
        path = handle.read().rstrip(b"\0").decode("utf-8")
    item = {"path": path, "signed": os.path.exists(prefix + ".entitlements")}
    plist_path = prefix + ".plist"
    if os.path.exists(plist_path):
        with open(plist_path, "rb") as handle:
            plist = plistlib.load(handle)
        item["bundle_identifier"] = plist.get("CFBundleIdentifier")
        item["bundle_version"] = plist.get("CFBundleVersion")
        item["short_version"] = plist.get("CFBundleShortVersionString")
        item["executable"] = plist.get("CFBundleExecutable")
    entitlements_path = prefix + ".entitlements"
    if os.path.exists(entitlements_path):
        raw = open(entitlements_path, "rb").read()
        try:
            item["entitlements"] = plistlib.loads(raw)
        except Exception:
            item["entitlements_base64"] = base64.b64encode(raw).decode("ascii")
    items.append(item)
with open(destination, "w", encoding="utf-8") as handle:
    json.dump({"format_version": 1, "bundles": items}, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY

# Remove nested signatures first. codesign reports an unsigned object as an error,
# so probe before removal and still delete all signature material afterwards.
while IFS= read -r -d '' candidate; do
  if file -b "$candidate" | grep -q 'Mach-O' && codesign -dv "$candidate" >/dev/null 2>&1; then
    codesign --remove-signature "$candidate"
  fi
done < <(find "$app" -depth -type f -print0)

while IFS= read -r -d '' bundle; do
  if codesign -dv "$bundle" >/dev/null 2>&1; then
    codesign --remove-signature "$bundle"
  fi
done < <(find "$app" -depth -type d \( -name '*.app' -o -name '*.appex' -o -name '*.framework' -o -name '*.xpc' \) -print0)

find "$app" -depth \( -name '_CodeSignature' -o -name 'SC_Info' \) -type d -exec rm -rf {} +
find "$app" -depth \( -name 'embedded.mobileprovision' -o -name 'CodeResources' \) -type f -delete

echo "$metadata"
