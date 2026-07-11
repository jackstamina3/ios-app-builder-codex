#!/usr/bin/env bash
set -euo pipefail

: "${SOURCE_DIR:?SOURCE_DIR is required}"
: "${ADAPTER_ENV_FILE:?ADAPTER_ENV_FILE is required}"
: "${SAFE_HOME:?SAFE_HOME is required}"

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
test -f "$SOURCE_DIR/MPVKit/README.md" || { echo "MPVKit README is missing" >&2; exit 1; }
grep -Eqi 'GPL|General Public License' "$SOURCE_DIR/MPVKit/README.md" || {
  echo "MPVKit has no explicit GPL license statement" >&2
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
GRADLE_OPTS=-Xmx1024m -Dfile.encoding=UTF-8 -XX:MaxMetaspaceSize=768m -Dorg.gradle.jvmargs=-Xmx4096m -Dkotlin.daemon.jvmargs=-Xmx3072m -Dkotlin.native.jvmArgs=-Xmx4096m
KOTLIN_DAEMON_JVMARGS=-Xmx3072m
ORG_GRADLE_PROJECT_org.gradle.jvmargs=-Xmx4096m -XX:MaxMetaspaceSize=1024m
ORG_GRADLE_PROJECT_kotlin.daemon.jvmargs=-Xmx3072m
ORG_GRADLE_PROJECT_kotlin.native.jvmArgs=-Xmx4096m
EOF
