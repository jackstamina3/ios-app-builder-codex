# Codex-Native Unsigned iOS IPA Builder

This public builder creates reproducible **unsigned** iOS IPAs from explicitly licensed or otherwise authorized public source. It does not download or repackage prebuilt IPAs and stores no Apple signing material. Public visibility permits free use of standard GitHub-hosted runners; workflow logs and published build artifacts must therefore be treated as public information.

Every build begins with an explicit user-selected repository and source ref. Files under `targets/` are immutable records of prior selections, never defaults for a later build. A selected ref is resolved to a full commit before a target record is created and dispatched.

The pinned MPVKit submodule predates that fork's standalone license file. The builder records this explicitly and checksum-pins the repository-level LGPL-3.0 license from MPVKit commit `ca111517f60e4631fd0b9a3fd0d03689e9f38b8a`; it never presents that later license file as if it existed in the older source tree.

The Nuvio adapter creates an empty, untracked `local.properties` compatibility stub. Nuvio's Gradle task declares that file optional and supplies empty runtime-service defaults, but Gradle 9.4 rejects the assigned missing input before the task runs. The adapter never adds credentials to the file and verifies that tracked upstream source remains unchanged.

The Nuvio targets use Xcode 26.3 and a physical-device Debug archive so they can build on GitHub's free 14 GB Intel runner. Their pinned Compose binaries reference the iOS 26 `UIViewLayoutRegion` API, which cannot link with Xcode 16.4's iOS 18.5 SDK. Kotlin/Native 2.3.0 Release devirtualization also exceeded even a 10.5 GB compiler heap; Debug avoids that optimizer while preserving the selected official source commit, `full` distribution, device platform, and unsigned packaging checks. The adapter installs checked-in memory bounds into its isolated `GRADLE_USER_HOME`, taking precedence over Nuvio's project-level 12/8/16 GB settings.

## Commands

```bash
bin/probe-source OWNER/REPOSITORY REF
python3 scripts/validate_target.py targets/SELECTED_TARGET.json
bin/build-target targets/SELECTED_TARGET.json
```

The build dispatcher requires a clean `main` exactly matching `origin/main`, the authorized public builder repository, and authenticated GitHub CLI. Successful artifacts are downloaded under `dist/REQUEST_ID/`, reverified locally, and reported with an absolute path and SHA-256.

## Threat model

Public source builds may execute arbitrary upstream Gradle, Xcode, dependency, or project-generation code. The builder uses ephemeral GitHub-hosted runners, no secrets, read-only tokens, nonpersistent checkout credentials, isolated directories, and a scrubbed child-process environment. This is not a complete sandbox. A separate fresh runner validates the quarantined IPA before publishing the success artifact.

## Output

Nuvio artifacts are named from the embedded application version, build number, and source commit. They are not signed and cannot be installed until a separate authorized signing process supplies a valid identity and provisioning profile.
