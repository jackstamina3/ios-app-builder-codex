#!/usr/bin/env bash
set -euo pipefail

: "${SOURCE_DIR:?SOURCE_DIR is required}"
: "${OUTPUT_DIR:?OUTPUT_DIR is required}"
: "${SOURCE_REPOSITORY:?SOURCE_REPOSITORY is required}"
: "${SOURCE_REF:?SOURCE_REF is required}"
: "${EXPECTED_SHA:?EXPECTED_SHA is required}"

mkdir -p "$OUTPUT_DIR"
projects="$(find "$SOURCE_DIR" -type d \( -name '*.xcodeproj' -o -name '*.xcworkspace' \) -not -path '*/Pods/*' -not -path '*/Carthage/*' -not -path '*/.build/*' -not -path '*.xcodeproj/project.xcworkspace' | sed "s#^$SOURCE_DIR/##" | sort -u | jq -Rsc 'split("\n")[:-1]')"
detected="$(for f in LICENSE LICENSE.md COPYING Podfile Podfile.lock Cartfile Package.swift Package.resolved project.yml Project.swift Gemfile package.json gradlew settings.gradle.kts pubspec.yaml iosApp/Configuration/Version.xcconfig .xcode-version mise.toml; do if [[ -e "$SOURCE_DIR/$f" ]]; then echo "$f"; fi; done | jq -Rsc 'split("\n")[:-1]')"
schemes='[]'
errors='[]'
while IFS= read -r container; do
  [[ -z "$container" ]] && continue
  flag=-project; [[ "$container" == *.xcworkspace ]] && flag=-workspace
  error_file="$OUTPUT_DIR/xcode-list-$(tr '/ ' '__' <<<"$container").log"
  if listing="$(xcodebuild "$flag" "$SOURCE_DIR/$container" -list -json 2>"$error_file")"; then
    schemes="$(jq -cn --arg path "$container" --argjson listing "$listing" --argjson prior "$schemes" '$prior + [{container:$path, listing:$listing}]')"
    rm -f "$error_file"
  else
    errors="$(jq -cn --arg path "$container" --arg message "$(tail -40 "$error_file")" --argjson prior "$errors" '$prior + [{container:$path,message:$message}]')"
  fi
done < <(jq -r '.[]' <<<"$projects")

ambiguities='[]'
if [[ "$(jq 'length' <<<"$schemes")" -gt 1 ]]; then
  ambiguities='["multiple viable Xcode containers require an explicit target manifest choice"]'
fi

jq -n --arg repository "$SOURCE_REPOSITORY" --arg ref "$SOURCE_REF" --arg commit "$EXPECTED_SHA" \
  --arg xcode "$(xcodebuild -version | tr '\n' ' ')" --arg sdk "$(xcrun --sdk iphoneos --show-sdk-version)" \
  --arg arch "$(uname -m)" --argjson containers "$projects" --argjson detected "$detected" --argjson schemes "$schemes" --argjson ambiguities "$ambiguities" --argjson errors "$errors" \
  '{repository:$repository,requested_ref:$ref,resolved_commit:$commit,runner_architecture:$arch,xcode:$xcode,iphoneos_sdk:$sdk,containers:$containers,detected_files:$detected,scheme_reports:$schemes,ambiguities:$ambiguities,errors:$errors}' \
  >"$OUTPUT_DIR/probe-report.json"
