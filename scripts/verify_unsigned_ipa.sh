#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 IPA [VERIFICATION_JSON]" >&2
  exit 64
}

environment_mode=false
if [[ $# -eq 0 ]]; then
  : "${INPUT_IPA:?INPUT_IPA is required in environment mode}"
  : "${OUTPUT_DIR:?OUTPUT_DIR is required in environment mode}"
  ipa=$INPUT_IPA
  summary="$OUTPUT_DIR/verification.json"
  environment_mode=true
elif [[ $# -ge 1 && $# -le 2 ]]; then
  ipa=$1
  summary=${2:-}
else
  usage
fi
[[ -f "$ipa" ]] || { echo "IPA not found: $ipa" >&2; exit 66; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 69; }

work=$(mktemp -d "${TMPDIR:-/tmp}/ipa-verify.XXXXXX")
trap 'rm -rf "$work"' EXIT
portable="$work/portable.json"

python3 - "$ipa" "$work/extracted" "$portable" <<'PY'
import hashlib
import json
import os
import plistlib
import re
import stat
import sys
import zipfile

ipa, destination, report_path = sys.argv[1:]
MAX_ENTRIES = 20000
MAX_FILE = 1024 * 1024 * 1024
MAX_TOTAL = 4 * 1024 * 1024 * 1024
FORBIDDEN_PARTS = {"_CodeSignature", "SC_Info"}
FORBIDDEN_FILES = {"embedded.mobileprovision", "CodeResources"}

def fail(message):
    raise SystemExit("IPA verification failed: " + message)

try:
    archive = zipfile.ZipFile(ipa)
except (OSError, zipfile.BadZipFile) as exc:
    fail(f"invalid ZIP archive: {exc}")
with archive:
    infos = archive.infolist()
    if not infos or len(infos) > MAX_ENTRIES:
        fail("archive entry count is outside allowed bounds")
    total = 0
    seen = set()
    for info in infos:
        name = info.filename
        if "\x00" in name or "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
            fail(f"unsafe absolute entry: {name!r}")
        parts = [part for part in name.replace("\\", "/").split("/") if part not in ("", ".")]
        if ".." in parts or not parts or parts[0] != "Payload":
            fail(f"unsafe or unexpected archive entry: {name!r}")
        normalized = "/".join(parts)
        if normalized in seen:
            fail(f"duplicate archive entry: {normalized}")
        seen.add(normalized)
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            fail(f"symbolic links are forbidden: {normalized}")
        if info.file_size > MAX_FILE:
            fail(f"entry exceeds size limit: {normalized}")
        total += info.file_size
        if total > MAX_TOTAL:
            fail("archive exceeds total uncompressed size limit")
        if FORBIDDEN_PARTS.intersection(parts) or (parts and parts[-1] in FORBIDDEN_FILES):
            fail(f"signing material remains: {normalized}")
    archive.extractall(destination)
    for info in infos:
        mode = info.external_attr >> 16
        if stat.S_ISREG(mode):
            parts = [part for part in info.filename.split("/") if part not in ("", ".")]
            os.chmod(os.path.join(destination, *parts), mode & 0o777)

payload = os.path.join(destination, "Payload")
apps = sorted(name for name in os.listdir(payload) if name.endswith(".app") and os.path.isdir(os.path.join(payload, name)))
if len(apps) != 1:
    fail(f"expected exactly one top-level .app, found {len(apps)}")
unexpected = sorted(name for name in os.listdir(payload) if name not in apps and name != ".DS_Store")
if unexpected:
    fail("unexpected top-level Payload content: " + ", ".join(unexpected))
MACH_MAGICS = {
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
}
def is_macho(path):
    with open(path, "rb") as handle:
        return handle.read(4) in MACH_MAGICS

app = os.path.join(payload, apps[0])
bundle_suffixes = (".app", ".appex", ".framework", ".xpc")
declared_executables = set()
main_plist = None
main_executable = None
for directory, dirnames, _ in os.walk(app):
    dirnames.sort()
    if not directory.endswith(bundle_suffixes):
        continue
    plist_path = os.path.join(directory, "Info.plist")
    try:
        with open(plist_path, "rb") as handle:
            bundle_plist = plistlib.load(handle)
    except Exception as exc:
        fail(f"invalid bundle Info.plist at {os.path.relpath(directory, destination)}: {exc}")
    executable_name = bundle_plist.get("CFBundleExecutable")
    if (not isinstance(executable_name, str) or not executable_name or
            executable_name in (".", "..") or "/" in executable_name or "\\" in executable_name):
        fail("invalid CFBundleExecutable in " + os.path.relpath(directory, destination))
    executable = os.path.join(directory, executable_name)
    if not os.path.isfile(executable) or not os.access(executable, os.X_OK) or not is_macho(executable):
        fail("declared bundle executable is missing or is not executable Mach-O: " +
             os.path.relpath(executable, destination))
    declared_executables.add(os.path.realpath(executable))
    if directory == app:
        main_plist = bundle_plist
        main_executable = executable

if main_plist is None or main_executable is None:
    fail("top-level application bundle metadata was not validated")
for directory, _, filenames in os.walk(app):
    for filename in filenames:
        path = os.path.join(directory, filename)
        if os.access(path, os.X_OK) and not is_macho(path):
            fail("unexpected non-Mach-O executable: " + os.path.relpath(path, destination))
        if is_macho(path) and os.path.realpath(path) not in declared_executables and not filename.endswith(".dylib"):
            fail("unexpected undeclared Mach-O executable: " + os.path.relpath(path, destination))

sha = hashlib.sha256()
with open(ipa, "rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        sha.update(chunk)
report = {
    "format_version": 1,
    "ipa": os.path.abspath(ipa),
    "sha256": sha.hexdigest(),
    "uncompressed_bytes": total,
    "entry_count": len(infos),
    "app": apps[0],
    "bundle_identifier": main_plist.get("CFBundleIdentifier"),
    "short_version": main_plist.get("CFBundleShortVersionString"),
    "bundle_version": main_plist.get("CFBundleVersion"),
    "main_executable": os.path.relpath(main_executable, destination),
    "declared_bundle_executables": sorted(os.path.relpath(path, destination) for path in declared_executables),
}
with open(report_path, "w", encoding="utf-8") as handle:
    json.dump(report, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY

# The portable pass validates archive structure and metadata. macOS additionally
# proves each Mach-O targets physical iOS and is not code-signed.
if [[ "$(uname -s)" == Darwin ]]; then
  extracted="$work/extracted"
  macho_count=0
  while IFS= read -r -d '' file; do
    kind=$(file -b "$file")
    if [[ "$kind" != *Mach-O* ]]; then
      if [[ -x "$file" ]]; then
        echo "IPA verification failed: executable file is not Mach-O: ${file#"$extracted"/}" >&2
        exit 1
      fi
      continue
    fi
    if grep -Eq '(^|[^A-Za-z0-9_])(i386|x86_64)([^A-Za-z0-9_]|$)' <<<"$kind"; then
      echo "IPA verification failed: simulator-only architecture remains: ${file#"$extracted"/}" >&2
      exit 1
    fi
    macho_count=$((macho_count + 1))
    if codesign -dv "$file" >/dev/null 2>&1; then
      echo "IPA verification failed: signed Mach-O remains: ${file#"$extracted"/}" >&2
      exit 1
    fi
    load_commands=$(otool -l "$file" 2>/dev/null || true)
    if grep -q 'LC_CODE_SIGNATURE' <<<"$load_commands"; then
      echo "IPA verification failed: LC_CODE_SIGNATURE remains: ${file#"$extracted"/}" >&2
      exit 1
    fi
    platform=""
    if command -v vtool >/dev/null; then
      platform=$(vtool -show-build "$file" 2>/dev/null || true)
      if grep -Eqi 'platform[[:space:]]+(IOSSIMULATOR|7)([[:space:]]|$)' <<<"$platform"; then
        echo "IPA verification failed: simulator Mach-O: ${file#"$extracted"/}" >&2
        exit 1
      fi
      platforms=$(sed -nE 's/^[[:space:]]*platform[[:space:]]+([^[:space:]]+).*$/\1/p' <<<"$platform")
      if [[ -n "$platforms" ]]; then
        while IFS= read -r item; do
          if [[ ! "$item" =~ ^(IOS|2)$ ]]; then
            echo "IPA verification failed: non-iOS Mach-O platform $item: ${file#"$extracted"/}" >&2
            exit 1
          fi
        done <<<"$platforms"
      elif grep -q 'LC_VERSION_MIN_IPHONEOS' <<<"$load_commands"; then
        :
      else
        echo "IPA verification failed: Mach-O has no physical iOS platform: ${file#"$extracted"/}" >&2
        exit 1
      fi
    elif grep -q 'LC_VERSION_MIN_IPHONEOS' <<<"$load_commands"; then
      :
    else
      echo "IPA verification failed: cannot prove physical iOS platform: ${file#"$extracted"/}" >&2
      exit 1
    fi
  done < <(find "$extracted/Payload" -type f -print0)
  if [[ $macho_count -eq 0 ]]; then
    echo "IPA verification failed: application contains no Mach-O binaries" >&2
    exit 1
  fi
fi

if [[ -n "$summary" ]]; then
  mkdir -p "$(dirname "$summary")"
  cp "$portable" "$summary"
else
  cat "$portable"
fi

manifest_path="$(dirname "$ipa")/build-manifest.json"
if [[ "$environment_mode" == true || -f "$manifest_path" ]]; then
  [[ -f "$manifest_path" ]] || { echo "IPA verification failed: quarantined build-manifest.json is missing" >&2; exit 1; }
  adjacent_ipa_count=$(find "$(dirname "$ipa")" -maxdepth 1 -type f -name '*.unsigned.ipa' | wc -l | tr -d ' ')
  [[ "$adjacent_ipa_count" == 1 ]] || { echo "IPA verification failed: expected exactly one adjacent unsigned IPA, found $adjacent_ipa_count" >&2; exit 1; }
  python3 - "$manifest_path" "$portable" "${TARGET_JSON:-}" "$ipa" "$environment_mode" <<'PY'
import json, os, sys
manifest_path, verification_path, target_path, ipa_path, environment_mode = sys.argv[1:]
with open(manifest_path, encoding="utf-8") as handle:
    manifest = json.load(handle)
with open(verification_path, encoding="utf-8") as handle:
    verification = json.load(handle)
artifact = manifest.get("artifact", {})
source = manifest.get("source", {})
if artifact.get("filename") != os.path.basename(ipa_path):
    raise SystemExit("IPA verification failed: manifest artifact filename mismatch")
if artifact.get("sha256") != verification.get("sha256"):
    raise SystemExit("IPA verification failed: manifest artifact SHA-256 mismatch")
if artifact.get("bytes") != os.path.getsize(ipa_path):
    raise SystemExit("IPA verification failed: manifest artifact size mismatch")
prior = manifest.get("verification", {})
if prior:
    for key in ("sha256", "app", "bundle_identifier", "short_version", "bundle_version", "main_executable"):
        if prior.get(key) != verification.get(key):
            raise SystemExit(f"IPA verification failed: prior verification {key} mismatch")
if target_path:
    with open(target_path, encoding="utf-8") as handle:
        target = json.load(handle)
    expected_app = target["output"]["expected_app_bundle"]
    expected_source = target["source"]
    if verification.get("app") != expected_app:
        raise SystemExit(f"IPA verification failed: expected {expected_app}, found {verification.get('app')}")
    for key in ("repository", "ref", "commit", "license_spdx"):
        if source.get(key) != expected_source.get(key):
            raise SystemExit(f"IPA verification failed: manifest source {key} mismatch")
elif manifest.get("status") != "verified_unsigned":
    raise SystemExit("IPA verification failed: published manifest is not verified_unsigned")
if environment_mode != "true":
    raise SystemExit(0)
manifest["verification"] = verification
manifest["verification"]["ipa"] = os.path.basename(manifest["verification"].get("ipa", ""))
manifest["status"] = "verified_unsigned"
temporary = manifest_path + ".tmp"
with open(temporary, "w", encoding="utf-8") as handle:
    json.dump(manifest, handle, indent=2, sort_keys=True)
    handle.write("\n")
os.replace(temporary, manifest_path)
PY
fi
