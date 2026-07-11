#!/usr/bin/env bash
set -euo pipefail

: "${SOURCE_REPOSITORY:?SOURCE_REPOSITORY is required}"
: "${SOURCE_REF:?SOURCE_REF is required}"
: "${EXPECTED_SHA:?EXPECTED_SHA is required}"
: "${SOURCE_DIR:?SOURCE_DIR is required}"

[[ "$SOURCE_REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "Invalid source repository" >&2; exit 1; }
[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "Invalid expected commit" >&2; exit 1; }
[[ "$SOURCE_REF" != -* && "$SOURCE_REF" != *$'\n'* ]] || { echo "Invalid source ref" >&2; exit 1; }

rm -rf "$SOURCE_DIR"
git init -q "$SOURCE_DIR"
git -C "$SOURCE_DIR" remote add origin "https://github.com/${SOURCE_REPOSITORY}.git"

if ! GIT_LFS_SKIP_SMUDGE=1 git -C "$SOURCE_DIR" fetch --no-tags --depth=1 origin "$EXPECTED_SHA"; then
  GIT_LFS_SKIP_SMUDGE=1 git -C "$SOURCE_DIR" fetch --no-tags --depth=1 origin "$SOURCE_REF"
fi
git -C "$SOURCE_DIR" checkout -q --detach FETCH_HEAD
actual_sha="$(git -C "$SOURCE_DIR" rev-parse HEAD)"
[[ "$actual_sha" == "$EXPECTED_SHA" ]] || { echo "Fetched $actual_sha, expected $EXPECTED_SHA" >&2; exit 1; }

if [[ -f "$SOURCE_DIR/.gitmodules" ]]; then
  while read -r key url; do
    [[ "$url" =~ ^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(\.git)?$ ]] || {
      echo "Unsupported submodule URL: $url" >&2
      exit 1
    }
    path_key="${key%.url}.path"
    path="$(git -C "$SOURCE_DIR" config -f .gitmodules --get "$path_key")"
    [[ -n "$path" && "$path" != /* && "$path" != *'..'* ]] || { echo "Unsafe submodule path: $path" >&2; exit 1; }
    entry="$(git -C "$SOURCE_DIR" ls-files -s -- "$path")"
    [[ "$entry" == 160000\ * ]] || { echo "Submodule is not pinned by a gitlink: $path" >&2; exit 1; }
    git -C "$SOURCE_DIR" -c protocol.allow=never -c protocol.https.allow=always submodule update --init --depth=1 -- "$path"
    actual="$(git -C "$SOURCE_DIR/$path" rev-parse HEAD)"
    expected="$(awk '{print $2}' <<<"$entry")"
    [[ "$actual" == "$expected" ]] || { echo "Submodule commit mismatch: $path" >&2; exit 1; }
  done < <(git -C "$SOURCE_DIR" config -f .gitmodules --get-regexp '^submodule\..*\.url$')
fi

if [[ -f "$SOURCE_DIR/.gitattributes" ]] && grep -Eq 'filter=lfs|filter[[:space:]]*=[[:space:]]*lfs' "$SOURCE_DIR/.gitattributes"; then
  command -v git-lfs >/dev/null 2>&1 || { echo "Git LFS is required" >&2; exit 1; }
  git -C "$SOURCE_DIR" lfs install --local
  git -C "$SOURCE_DIR" lfs pull
  git -C "$SOURCE_DIR" lfs fsck
fi

python3 - "$SOURCE_DIR" "${SUBMODULE_STATUS_FILE:-$SOURCE_DIR/submodules.txt}" <<'PY'
import pathlib, subprocess, sys
root, output = map(pathlib.Path, sys.argv[1:])
declared = set()
modules = root / ".gitmodules"
if modules.exists():
    raw = subprocess.run(
        ["git", "-C", str(root), "config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$"],
        capture_output=True, text=True,
    )
    for line in raw.stdout.splitlines():
        _, path = line.split(None, 1)
        declared.add(path)
lines = []
for entry in subprocess.check_output(["git", "-C", str(root), "ls-files", "-s"], text=True).splitlines():
    fields = entry.split(None, 3)
    if len(fields) == 4 and fields[0] == "160000":
        status = "declared" if fields[3] in declared else "orphan-ignored"
        lines.append(f"{fields[1]} {fields[3]} {status}")
output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
PY
