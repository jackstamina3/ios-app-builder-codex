#!/usr/bin/env bash
set -euo pipefail

: "${BUILDER_DIR:?BUILDER_DIR is required}"
: "${SOURCE_DIR:?SOURCE_DIR is required}"
: "${BUILD_DIR:?BUILD_DIR is required}"
: "${OUTPUT_DIR:?OUTPUT_DIR is required}"
: "${DIAGNOSTICS_DIR:?DIAGNOSTICS_DIR is required}"
: "${TARGET_JSON:?TARGET_JSON is required}"
: "${SAFE_HOME:?SAFE_HOME is required}"
: "${JAVA_HOME:?JAVA_HOME is required}"
: "${ANDROID_SDK_ROOT:?ANDROID_SDK_ROOT is required}"

mkdir -p "$BUILD_DIR" "$OUTPUT_DIR" "$DIAGNOSTICS_DIR" "$SAFE_HOME/.android"
target_abs="$(cd "$(dirname "$TARGET_JSON")" && pwd)/$(basename "$TARGET_JSON")"
source_root="$(cd "$SOURCE_DIR" && pwd)"

compile_sdk="$(jq -r '.android_sdk.compile_sdk' "$target_abs")"
build_tools="$(jq -r '.android_sdk.build_tools' "$target_abs")"
ndk_version="$(jq -r '.android_sdk.ndk' "$target_abs")"
working_directory="$(jq -r '.working_directory' "$target_abs")"
gradle_wrapper="$(jq -r '.gradle.wrapper' "$target_abs")"
gradle_sha="$(jq -r '.gradle.distribution_sha256' "$target_abs")"
gradle_task="$(jq -r '.gradle.task' "$target_abs")"
adapter="$(jq -r '.bootstrap.adapter // empty' "$target_abs")"
expected_apk="$(jq -r '.output.expected_apk' "$target_abs")"
final_name="$(jq -r '.output.final_name' "$target_abs")"

[[ -f "$ANDROID_SDK_ROOT/platforms/android-$compile_sdk/android.jar" ]] || {
  echo "Android platform $compile_sdk is not installed" >&2; exit 69;
}
build_tools_dir="$ANDROID_SDK_ROOT/build-tools/$build_tools"
apksigner="$build_tools_dir/apksigner"
zipalign="$build_tools_dir/zipalign"
apkanalyzer="$ANDROID_SDK_ROOT/cmdline-tools/latest/bin/apkanalyzer"
[[ -x "$apksigner" && -x "$zipalign" && -x "$apkanalyzer" ]] || {
  echo "Android SDK build-tools $build_tools and cmdline-tools/latest are required" >&2; exit 69;
}
[[ -d "$ANDROID_SDK_ROOT/ndk/$ndk_version" ]] || {
  echo "Android NDK $ndk_version is not installed" >&2; exit 69;
}
[[ -x "$JAVA_HOME/bin/java" && -x "$JAVA_HOME/bin/keytool" ]] || {
  echo "JAVA_HOME must point to a complete JDK" >&2; exit 69;
}
java_major="$("$JAVA_HOME/bin/java" -version 2>&1 | sed -n '1s/.*version "\([0-9]*\).*/\1/p')"
[[ "$java_major" == "17" ]] || { echo "Target requires Java 17, found Java $java_major" >&2; exit 69; }

wrapper_path="$source_root/$gradle_wrapper"
[[ -x "$wrapper_path" ]] || { echo "Pinned Gradle wrapper is missing or not executable" >&2; exit 1; }
wrapper_properties="$(dirname "$wrapper_path")/gradle/wrapper/gradle-wrapper.properties"
[[ -f "$wrapper_properties" ]] || { echo "Gradle wrapper properties are missing" >&2; exit 1; }
distribution_url="$(sed -n 's/^distributionUrl=//p' "$wrapper_properties" | sed 's#\\:#:#g')"
[[ "$distribution_url" =~ ^https://services\.gradle\.org/distributions/gradle-[0-9.]+-bin\.zip$ ]] || {
  echo "Gradle wrapper uses a non-allowlisted distribution URL" >&2; exit 1;
}
[[ "$gradle_sha" =~ ^[0-9a-f]{64}$ ]]

patch_path="$(jq -r '.source_patch.path // empty' "$target_abs")"
if [[ -n "$patch_path" ]]; then
  patch_sha="$(jq -r '.source_patch.sha256' "$target_abs")"
  patch_file="$BUILDER_DIR/$patch_path"
  actual_patch_sha="$(shasum -a 256 "$patch_file" | awk '{print $1}')"
  [[ "$actual_patch_sha" == "$patch_sha" ]] || { echo "Approved source patch checksum mismatch" >&2; exit 1; }
  git -C "$source_root" apply --check "$patch_file"
  git -C "$source_root" apply "$patch_file"
fi
approved_diff="$BUILD_DIR/approved-source.diff"
git -C "$source_root" diff --binary --no-ext-diff > "$approved_diff"
approved_diff_sha="$(shasum -a 256 "$approved_diff" | awk '{print $1}')"

if [[ -n "$adapter" ]]; then
  SOURCE_DIR="$source_root" TARGET_JSON="$target_abs" BUILDER_DIR="$BUILDER_DIR" \
    "$BUILDER_DIR/$adapter"
fi
current_diff_sha="$(git -C "$source_root" diff --binary --no-ext-diff | shasum -a 256 | awk '{print $1}')"
[[ "$current_diff_sha" == "$approved_diff_sha" ]] || { echo "Adapter changed source outside the approved patch" >&2; exit 1; }

# Build with a fresh, disposable Android debug identity. The password is public
# and fixed because this key is test-only and deleted with the isolated workdir.
keystore="$SAFE_HOME/.android/debug.keystore"
"$JAVA_HOME/bin/keytool" -genkeypair -noprompt \
  -keystore "$keystore" -storepass android -keypass android -alias androiddebugkey \
  -keyalg RSA -keysize 2048 -validity 30 \
  -dname "CN=Ephemeral Fire TV Test Build,OU=Disposable,O=Codex Builder,C=US" >/dev/null 2>&1
cert_sha="$("$JAVA_HOME/bin/keytool" -J-Duser.language=en -list -v -keystore "$keystore" \
  -storepass android -alias androiddebugkey | awk -F': ' '/SHA256:/{gsub(":", "", $2); print tolower($2); exit}')"
[[ "$cert_sha" =~ ^[0-9a-f]{64}$ ]] || { echo "Could not derive ephemeral signer certificate digest" >&2; exit 1; }
printf '%s\n' "$cert_sha" > "$OUTPUT_DIR/ephemeral-certificate-sha256.txt"

# Do not execute a downloaded Gradle distribution before verifying its official
# checksum. The checked-in wrapper selects the URL; the committed target pins it.
gradle_zip="$BUILD_DIR/gradle-distribution.zip"
curl --fail --location --proto '=https' --tlsv1.2 "$distribution_url" --output "$gradle_zip"
actual_gradle_sha="$(shasum -a 256 "$gradle_zip" | awk '{print $1}')"
[[ "$actual_gradle_sha" == "$gradle_sha" ]] || { echo "Gradle distribution checksum mismatch" >&2; exit 1; }
gradle_dir="$BUILD_DIR/gradle"
mkdir -p "$gradle_dir"
unzip -q "$gradle_zip" -d "$gradle_dir"
gradle_bin="$(find "$gradle_dir" -mindepth 2 -maxdepth 2 -type f -path '*/bin/gradle' -print -quit)"
[[ -x "$gradle_bin" ]] || { echo "Verified Gradle distribution did not contain bin/gradle" >&2; exit 1; }

while IFS= read -r entry; do
  export "$entry"
done < <(jq -r '.build_environment | to_entries[] | "\(.key)=\(.value)"' "$target_abs")

# Builder-controlled values always win over target environment values.
export HOME="$SAFE_HOME"
export GRADLE_USER_HOME="$BUILD_DIR/gradle-home"
export JAVA_HOME ANDROID_SDK_ROOT
export ANDROID_HOME="$ANDROID_SDK_ROOT"
export ANDROID_NDK_HOME="$ANDROID_SDK_ROOT/ndk/$ndk_version"
export CI=true
export CI_USE_DEBUG_SIGNING=true
export SENTRY_AUTH_TOKEN=
export SENTRY_ORG=
export SENTRY_PROJECT=
mkdir -p "$GRADLE_USER_HOME"

project_dir="$source_root/$working_directory"
[[ -d "$project_dir" ]] || { echo "Working directory does not exist" >&2; exit 1; }
"$gradle_bin" --no-daemon --console=plain --stacktrace \
  -Dorg.gradle.jvmargs='-Xmx6g -XX:MaxMetaspaceSize=1g -Dfile.encoding=UTF-8' \
  -Dkotlin.daemon.jvm.options='-Xmx2g' \
  -p "$project_dir" "$gradle_task"

post_build_diff_sha="$(git -C "$source_root" diff --binary --no-ext-diff | shasum -a 256 | awk '{print $1}')"
[[ "$post_build_diff_sha" == "$approved_diff_sha" ]] || { echo "Build changed tracked source files" >&2; exit 1; }

source_apk="$source_root/$expected_apk"
[[ -f "$source_apk" && ! -L "$source_apk" ]] || { echo "Expected APK was not produced: $expected_apk" >&2; exit 1; }
resolved_apk="$(cd "$(dirname "$source_apk")" && pwd)/$(basename "$source_apk")"
[[ "$resolved_apk" == "$source_root"/* ]] || { echo "Expected APK escaped source root" >&2; exit 1; }
aligned="$BUILD_DIR/aligned.apk"
final_apk="$OUTPUT_DIR/$final_name"
"$zipalign" -f -p 4 "$source_apk" "$aligned"
"$apksigner" sign \
  --ks "$keystore" --ks-key-alias androiddebugkey \
  --ks-pass pass:android --key-pass pass:android \
  --v1-signing-enabled true --v2-signing-enabled true --v3-signing-enabled true \
  --out "$final_apk" "$aligned"

python3 "$BUILDER_DIR/scripts/verify_android_apk.py" "$final_apk" "$target_abs" \
  --apkanalyzer "$apkanalyzer" --apksigner "$apksigner" \
  --expected-cert-sha256 "$cert_sha" --output "$OUTPUT_DIR/build-verification.json" >/dev/null

{
  "$JAVA_HOME/bin/java" -version
  "$gradle_bin" --version
  printf 'android_sdk=%s\ncompile_sdk=%s\nbuild_tools=%s\nndk=%s\n' \
    "$ANDROID_SDK_ROOT" "$compile_sdk" "$build_tools" "$ndk_version"
  printf 'source_commit=%s\nsource_diff_sha256=%s\n' \
    "$(git -C "$source_root" rev-parse HEAD)" "$approved_diff_sha"
} > "$DIAGNOSTICS_DIR/toolchain.txt" 2>&1

printf 'Test-signed APK: %s\nEphemeral certificate SHA-256: %s\n' "$final_apk" "$cert_sha"
