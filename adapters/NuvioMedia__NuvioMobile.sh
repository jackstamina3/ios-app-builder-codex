#!/usr/bin/env bash
set -euo pipefail

: "${SOURCE_DIR:?SOURCE_DIR is required}"
: "${ADAPTER_ENV_FILE:?ADAPTER_ENV_FILE is required}"
: "${SAFE_HOME:?SAFE_HOME is required}"
: "${BUILDER_DIR:?BUILDER_DIR is required}"

required=(
  gradlew
  gradle/wrapper/gradle-wrapper.properties
  settings.gradle.kts
  build.gradle.kts
  iosApp/iosApp.xcodeproj/project.pbxproj
  iosApp/Configuration/Version.xcconfig
  MPVKit/Package.swift
)
for path in "${required[@]}"; do
  test -e "$SOURCE_DIR/$path" || { echo "Nuvio prerequisite missing: $path" >&2; exit 1; }
done
test -x "$SOURCE_DIR/gradlew" || { echo "gradlew is not executable" >&2; exit 1; }

# Nuvio's GenerateRuntimeConfigsTask marks local.properties optional, but Gradle
# 9.4 validates its assigned RegularFileProperty before the task can apply its
# built-in empty defaults. Supply the conventional untracked local config stub;
# never populate it with credentials and prove that tracked source stays clean.
test ! -e "$SOURCE_DIR/local.properties" || {
  echo "Unexpected upstream local.properties file" >&2
  exit 1
}
touch "$SOURCE_DIR/local.properties"
if [[ "$(jq -r '.source_patch == null' "$TARGET_JSON")" == true ]]; then
  [[ -z "$(git -C "$SOURCE_DIR" status --porcelain --untracked-files=no)" ]] || {
    echo "Tracked source changed during Nuvio bootstrap" >&2
    exit 1
  }
else
  git -C "$SOURCE_DIR" diff --check
  [[ -n "$(git -C "$SOURCE_DIR" status --porcelain --untracked-files=no)" ]] || {
    echo "Declared Nuvio source patch is not applied" >&2
    exit 1
  }
fi

mpvkit_license_url='https://raw.githubusercontent.com/NuvioMedia/MPVKit/ca111517f60e4631fd0b9a3fd0d03689e9f38b8a/LICENSE'
mpvkit_license_sha='ea8af5e789cb2d4e9b10bce3874982ade163b749b6bfbdb32e2df21c4d106de1'
license_basis_dir="$SAFE_HOME/license-basis"
mkdir -p "$license_basis_dir"
curl --fail --location --proto '=https' --tlsv1.2 "$mpvkit_license_url" --output "$license_basis_dir/MPVKit-LICENSE"
[[ "$(shasum -a 256 "$license_basis_dir/MPVKit-LICENSE" | awk '{print $1}')" == "$mpvkit_license_sha" ]] || {
  echo "MPVKit repository-level license checksum mismatch" >&2
  exit 1
}
grep -Eq '^distributionUrl=https\\://services\.gradle\.org/distributions/gradle-[0-9.]+-(bin|all)\.zip$' "$SOURCE_DIR/gradle/wrapper/gradle-wrapper.properties" || {
  echo "Gradle wrapper uses an unsupported distribution URL" >&2
  exit 1
}
gradle_url='https://services.gradle.org/distributions/gradle-9.4.1-bin.zip'
gradle_sha='2ab2958f2a1e51120c326cad6f385153bb11ee93b3c216c5fccebfdfbb7ec6cb'
grep -Fxq 'distributionUrl=https\://services.gradle.org/distributions/gradle-9.4.1-bin.zip' "$SOURCE_DIR/gradle/wrapper/gradle-wrapper.properties" || {
  echo "Nuvio no longer uses the reviewed Gradle 9.4.1 distribution" >&2
  exit 1
}
gradle_hash="$(python3 - "$gradle_url" <<'PY'
import hashlib, sys
value = int.from_bytes(hashlib.md5(sys.argv[1].encode()).digest(), "big")
digits = "0123456789abcdefghijklmnopqrstuvwxyz"
result = ""
while value:
    value, remainder = divmod(value, 36)
    result = digits[remainder] + result
print(result or "0")
PY
)"
gradle_home="$SAFE_HOME/.gradle"
distribution_dir="$gradle_home/wrapper/dists/gradle-9.4.1-bin/$gradle_hash"
distribution_zip="$distribution_dir/gradle-9.4.1-bin.zip"
mkdir -p "$distribution_dir"
cp "$BUILDER_DIR/adapters/NuvioMedia__NuvioMobile.gradle.properties" "$gradle_home/gradle.properties"
if [[ ! -f "$distribution_zip" ]] || [[ "$(shasum -a 256 "$distribution_zip" | awk '{print $1}')" != "$gradle_sha" ]]; then
  temporary="$distribution_zip.download"
  rm -f "$temporary"
  curl --fail --location --proto '=https' --tlsv1.2 "$gradle_url" --output "$temporary"
  [[ "$(shasum -a 256 "$temporary" | awk '{print $1}')" == "$gradle_sha" ]] || {
    rm -f "$temporary"
    echo "Gradle distribution checksum mismatch" >&2
    exit 1
  }
  mv "$temporary" "$distribution_zip"
fi

java_home="$(/usr/libexec/java_home -v 17)"
test -x "$java_home/bin/java" || { echo "Java 17 is unavailable" >&2; exit 1; }

cat >"$ADAPTER_ENV_FILE" <<EOF
JAVA_HOME=$java_home
GRADLE_USER_HOME=$gradle_home
GRADLE_OPTS=-Xmx128m -Dfile.encoding=UTF-8 -XX:MaxMetaspaceSize=192m
KOTLIN_DAEMON_JVMARGS=-Xmx256m
EOF
