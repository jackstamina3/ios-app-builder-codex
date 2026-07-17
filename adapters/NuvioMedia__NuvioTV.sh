#!/usr/bin/env bash
set -euo pipefail

: "${SOURCE_DIR:?SOURCE_DIR is required}"
: "${TARGET_JSON:?TARGET_JSON is required}"

[[ -d "$SOURCE_DIR" && -f "$TARGET_JSON" ]]
for relative in \
  gradlew \
  settings.gradle.kts \
  gradle/wrapper/gradle-wrapper.jar \
  gradle/wrapper/gradle-wrapper.properties \
  gradle/libs.versions.toml \
  app/build.gradle.kts \
  app/src/main/AndroidManifest.xml; do
  [[ -f "$SOURCE_DIR/$relative" ]] || { echo "NuvioTV source is missing $relative" >&2; exit 1; }
done
[[ -x "$SOURCE_DIR/gradlew" ]] || { echo "NuvioTV gradlew is not executable" >&2; exit 1; }

wrapper="$SOURCE_DIR/gradle/wrapper/gradle-wrapper.properties"
build_file="$SOURCE_DIR/app/build.gradle.kts"
manifest="$SOURCE_DIR/app/src/main/AndroidManifest.xml"
versions="$SOURCE_DIR/gradle/libs.versions.toml"

grep -Fxq 'distributionUrl=https\://services.gradle.org/distributions/gradle-8.13-bin.zip' "$wrapper"
grep -Fq 'agp = "8.13.2"' "$versions"
grep -Fq 'compileSdk = 36' "$build_file"
grep -Fq 'ndkVersion = "29.0.14206865"' "$build_file"
grep -Fq 'applicationId = "com.nuvio.tv"' "$build_file"
grep -Fq 'versionCode = 1035' "$build_file"
grep -Fq 'versionName = "0.7.17-beta"' "$build_file"
grep -Fq 'create("full")' "$build_file"
grep -Fq 'include("armeabi-v7a", "arm64-v8a", "x86", "x86_64")' "$build_file"
grep -Fq 'env("CI_USE_DEBUG_SIGNING")' "$build_file"
grep -Fq 'android.intent.category.LEANBACK_LAUNCHER' "$manifest"
grep -Fq 'android:banner="@mipmap/banner"' "$manifest"
grep -Fq 'android.hardware.touchscreen' "$manifest"

# These bundled AARs and the selected native library are required source inputs.
for required in \
  app/libs/lib-common-release.aar \
  app/libs/lib-exoplayer-release.aar \
  app/libs/quickjs-kt-android-1.0.5-nuvio.aar \
  app/src/main/jniLibs/armeabi-v7a/libtorrserver.so; do
  [[ -f "$SOURCE_DIR/$required" ]] || { echo "NuvioTV source is missing required input $required" >&2; exit 1; }
done

git -C "$SOURCE_DIR" diff --quiet --
git -C "$SOURCE_DIR" diff --cached --quiet --
printf 'NuvioTV adapter validation passed; no source files were changed.\n'
