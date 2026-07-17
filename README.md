# Reproducible iOS IPA and Android TV APK Builder

This public repository builds two kinds of artifacts from explicitly licensed or otherwise authorized public source:

- unsigned physical-device iOS IPAs; and
- disposable-key, test-signed Android APKs for selected Android TV/Fire TV profiles.

It never downloads or repackages a prebuilt application. Public visibility permits free use of standard GitHub-hosted runners, so workflow logs and artifacts must be treated as public information. Upstream build code is untrusted code: isolated directories and scrubbed environments reduce credential exposure but are not sandboxes.

Every build begins with an explicit user-selected repository, source ref, variant, and build route. Files under `targets/` and `targets/android/` are immutable records of prior selections, never defaults for a later build. A selected ref is resolved to a full commit before a target record is created and dispatched.

## Android TV and Fire TV

Android targets use the separate closed contract in `schemas/android-target.schema.json`. The cloud route builds on `ubuntu-24.04`, signs with a key created only inside that run, quarantines the APK, and performs the final TV, ABI, manifest, archive, and signing checks on a fresh runner. The local route requires Java 17 and the target's exact Android SDK, build-tools, and NDK versions. It does not install missing tools.

The first immutable Android record is official `NuvioMedia/NuvioTV` tag `0.7.18` at commit `849f7020b85971dc3abf7870060737053165ea16`, variant `full`, for the Fire TV Stick 4K Max (2nd generation). The source embeds version `0.7.17-beta` (code `1035`); both versions are recorded in the result. The device profile pins Amazon model `AFTKRT`, Fire OS 8/API 30, and `armeabi-v7a`.

```bash
bin/probe-android-source OWNER/REPOSITORY REF
python3 scripts/validate_android_target.py targets/android/SELECTED_TARGET.json

# Free GitHub-hosted build plus fresh-runner verification
bin/build-apk targets/android/SELECTED_TARGET.json

# Local Java/Android SDK build
bin/build-apk-local targets/android/SELECTED_TARGET.json --android-sdk "$HOME/Library/Android/sdk"

# First invocation verifies and prints an exact device-side-effect plan only.
bin/install-firetv ARTIFACT.apk --target targets/android/SELECTED_TARGET.json --device FIRE_TV_IP
# Installation requires rerunning with the printed --confirm-install APK_SHA256.
```

The installer refuses to replace or uninstall an existing package. A successful confirmed install verifies model/API/ABI, launches the Leanback activity, sends a bounded DPAD smoke test, and writes a filtered local JSON report.

See [Amazon's device specification](https://developer.amazon.com/docs/device-specs/device-specifications-fire-tv-streaming-media-player.html?v=ftvstick4kmax_gen2_16), [Fire OS 8 documentation](https://developer.amazon.com/docs/fire-tv/fire-os-8.html), and [ADB installation guide](https://developer.amazon.com/docs/fire-tv/installing-and-running-your-app.html).

## iOS

The `0.2.22` Nuvio target carries a user-approved, checksum-pinned four-line source patch because the official tag contains two misplaced `LazyListScope` braces and does not compile as published. The patch path, checksum, and reason are part of the target contract and final build manifest.

The pinned MPVKit submodule predates that fork's standalone license file. The builder records this explicitly and checksum-pins the repository-level LGPL-3.0 license from MPVKit commit `ca111517f60e4631fd0b9a3fd0d03689e9f38b8a`; it never presents that later license file as if it existed in the older source tree.

The Nuvio adapter creates an empty, untracked `local.properties` compatibility stub. Nuvio's Gradle task declares that file optional and supplies empty runtime-service defaults, but Gradle 9.4 rejects the assigned missing input before the task runs. The adapter never adds credentials to the file and verifies that tracked upstream source remains unchanged.

The Nuvio targets use an explicitly recorded Xcode 26 version and a physical-device Debug archive. The exact Xcode 26.6 record pins Apple build `17F113`, uses GitHub's free 14 GB `macos-26-intel` runner, and can also use a local installation at `/Applications/Xcode_26.6.app`. Their pinned Compose binaries reference the iOS 26 `UIViewLayoutRegion` API, which cannot link with Xcode 16.4's iOS 18.5 SDK. Kotlin/Native 2.3.0 Release devirtualization also exceeded even a 10.5 GB compiler heap; Debug avoids that optimizer while preserving the selected official source commit, `full` distribution, device platform, and unsigned packaging checks. The adapter installs checked-in memory bounds into its isolated `GRADLE_USER_HOME`, taking precedence over Nuvio's project-level 12/8/16 GB settings.

The existing iOS route remains unchanged. NuvioMobile's recorded source workaround and Xcode requirements apply only to its immutable historical targets.

### iOS commands

```bash
bin/probe-source OWNER/REPOSITORY REF
python3 scripts/validate_target.py targets/SELECTED_TARGET.json
# Free ephemeral GitHub-hosted build with fresh-runner verification
bin/build-target targets/SELECTED_TARGET.json

# Local build using the target's exact Xcode version
bin/build-local targets/SELECTED_TARGET.json --xcode /Applications/Xcode_VERSION.app
```

Both dispatchers require a clean working tree and an explicitly selected committed target. The cloud dispatcher additionally requires `main` to match `origin/main`, the authorized public builder repository, and authenticated GitHub CLI. Successful artifacts are written under `dist/REQUEST_ID/`, verified, and reported with an absolute path and SHA-256.

`bin/build-local` requires macOS and the exact Xcode version declared by the selected target. It clones the immutable source into a temporary directory, applies any checksum-pinned target patch, builds a generic physical-device archive with signing disabled, strips nested signatures, packages one IPA, and verifies it twice locally. It does not use GitHub Actions and can be faster on a sufficiently capable Mac. It will not install Xcode or silently substitute another Xcode version.

## Threat model

Public source builds may execute arbitrary upstream Gradle, Xcode, dependency, or project-generation code. The builder uses ephemeral GitHub-hosted runners, no secrets, read-only tokens, nonpersistent checkout credentials, isolated directories, and scrubbed child-process environments. This is not a complete sandbox. Separate fresh runners validate quarantined cloud artifacts before publishing them.

The local-Xcode route executes that untrusted upstream code on the user's Mac. It uses isolated temporary directories and a scrubbed child-process environment, but those controls are not a sandbox. Unlike the cloud route, local verification occurs on the same machine as the build and therefore does not provide fresh-runner isolation. Do not run the local route while sensitive credentials are exposed to broadly readable files or services.

## Output

NuvioMobile iOS artifacts are named from the embedded application version, build number, and source commit. They are not signed and cannot be installed until a separate authorized signing process supplies a valid identity and provisioning profile.

Android outputs end in `.test-signed.apk`. Their disposable certificate is suitable only for sideloaded testing; it is not a release identity and cannot provide stable upgrade continuity. The artifact includes `SHA256SUMS`, a verified build manifest, fresh-runner verification, the source license, target record, and relevant Gradle lock/version files.
