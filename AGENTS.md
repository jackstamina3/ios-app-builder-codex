# Public Source Artifact Builder Guidance

This public repository reproducibly builds unsigned iOS IPAs and disposable-key Android TV test APKs from authorized public source. Treat workflow logs and artifacts as public. Never download, decrypt, patch, or repackage an App Store, Play Store, release APK, or other proprietary application artifact.

## Required operating procedure

1. At the beginning of every build request, ask the user which repository and source ref/tag to target. Also ask for any project variant when more than one exists. Never infer a target from an existing manifest, reuse the previous target, or interpret “latest” without confirming the resolved release with the user.
2. Confirm an explicit source license or the user's authorization.
3. Resolve the user-selected source ref to a full commit SHA and generate or update a committed target under `targets/`. Committed targets are immutable build records, not defaults.
4. Validate iOS with `python3 scripts/validate_target.py TARGET`; validate Android with `python3 scripts/validate_android_target.py TARGET`.
5. Probe uncertain projects with `bin/probe-source OWNER/REPOSITORY REF` or `bin/probe-android-source OWNER/REPOSITORY REF`; never guess among Xcode schemes, Gradle applications, variants, or outputs.
6. Ask whether the user wants the free ephemeral GitHub route (`bin/build-target` or `bin/build-apk`) or the corresponding local route (`bin/build-local` or `bin/build-apk-local`). Never choose local execution implicitly. Inspect diagnostics and make only evidence-based manifest or adapter changes.
7. For local builds, confirm the exact target Xcode or Java/Android SDK toolchain is installed and warn that authorized upstream build code executes on the user's Mac without fresh-runner isolation.
8. Report source ref and SHA, license, build mode, runner when applicable, selected toolchain, embedded app version, application/bundle ID, target device/ABI when applicable, absolute artifact path, SHA-256, and signing status.

## Security invariants

- Workflows are manual-only, read-only, secret-free, and use GitHub-hosted runners plus full-SHA-pinned GitHub-owned actions.
- Never add persistent signing identities, profiles, Apple credentials, private dependencies, shared dependency caches, provisioning updates, free-form workflow commands, `sudo`, or unverified tool downloads.
- Build settings cannot override mandatory no-sign values. Do not claim an unsigned IPA is installable without downstream signing.
- Android target manifests cannot supply credentials or signing settings. Test APKs are re-signed with a run-local disposable key, must use v2 or newer signatures, and must never be described as release-signed.
- Never install, replace, or uninstall a Fire TV package without the external-action confirmation gate. Existing packages are not replaced or removed by the helper.
- Environment scrubbing is defense in depth, not a sandbox. Third-party build code remains untrusted; final verification therefore runs on a fresh runner.
- Stop on binary-only source, missing authorization, unavailable pinned source/Xcode, private dependencies, ambiguous schemes, simulator-only output, or residual signing material.

## Verification

Run `python3 -m unittest discover -s tests`, `bash -n` on shell files, Python compilation, and `python3 tests/test_workflow_policy.py` after changes.
