# Codex-Native Unsigned iOS IPA Builder

This public builder creates reproducible **unsigned** iOS IPAs from explicitly licensed or otherwise authorized public source. It does not download or repackage prebuilt IPAs and stores no Apple signing material. Public visibility permits free use of standard GitHub-hosted runners; workflow logs and published build artifacts must therefore be treated as public information.

The initial target is official NuvioMobile release `0.2.20` at commit `70004b7b825a8b9fa672a40ec92062884ddf4901`, built as the upstream `full` iOS distribution with Xcode 16.4 on `macos-15-intel`. The source embeds app version `1.1.20` build `92`, so both versions are reported. Xcode 16.4 supplies the required Swift 6.1 syntax and is the newest complete Xcode 16 toolchain on the GitHub runner image.

The pinned MPVKit submodule predates that fork's standalone license file. The builder records this explicitly and checksum-pins the repository-level LGPL-3.0 license from MPVKit commit `ca111517f60e4631fd0b9a3fd0d03689e9f38b8a`; it never presents that later license file as if it existed in the older source tree.

The Nuvio adapter creates an empty, untracked `local.properties` compatibility stub. Nuvio's Gradle task declares that file optional and supplies empty runtime-service defaults, but Gradle 9.4 rejects the assigned missing input before the task runs. The adapter never adds credentials to the file and verifies that tracked upstream source remains unchanged.

The committed Nuvio target uses a physical-device Debug archive so it can build on GitHub's free 14 GB Intel runner. Nuvio 0.2.20's Kotlin/Native 2.3.0 Release devirtualization exceeded even a 10.5 GB compiler heap; using Debug avoids that optimizer while preserving the exact official source commit, `full` distribution, device platform, and unsigned packaging checks. The adapter installs checked-in memory bounds into its isolated `GRADLE_USER_HOME`, taking precedence over Nuvio's project-level 12/8/16 GB settings.

## Commands

```bash
python3 scripts/validate_target.py targets/NuvioMedia__NuvioMobile__70004b7.json
bin/probe-source NuvioMedia/NuvioMobile 0.2.20
bin/build-target targets/NuvioMedia__NuvioMobile__70004b7.json
```

The build dispatcher requires a clean `main` exactly matching `origin/main`, the authorized public builder repository, and authenticated GitHub CLI. Successful artifacts are downloaded under `dist/REQUEST_ID/`, reverified locally, and reported with an absolute path and SHA-256.

## Threat model

Public source builds may execute arbitrary upstream Gradle, Xcode, dependency, or project-generation code. The builder uses ephemeral GitHub-hosted runners, no secrets, read-only tokens, nonpersistent checkout credentials, isolated directories, and a scrubbed child-process environment. This is not a complete sandbox. A separate fresh runner validates the quarantined IPA before publishing the success artifact.

## Output

The Nuvio artifact is named `Nuvio-1.1.20-92-70004b7.unsigned.ipa`. It is not signed and cannot be installed until a separate authorized signing process supplies a valid identity and provisioning profile.
