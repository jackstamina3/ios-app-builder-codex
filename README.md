# Codex-Native Unsigned iOS IPA Builder

This private builder creates reproducible **unsigned** iOS IPAs from explicitly licensed or otherwise authorized public source. It does not download or repackage prebuilt IPAs and stores no Apple signing material.

The initial target is official NuvioMobile release `0.2.20` at commit `70004b7b825a8b9fa672a40ec92062884ddf4901`, built as the upstream `full` iOS distribution with Xcode 16.2 on `macos-15-intel`. The source embeds app version `1.1.20` build `92`, so both versions are reported.

## Commands

```bash
python3 scripts/validate_target.py targets/NuvioMedia__NuvioMobile__70004b7.json
bin/probe-source NuvioMedia/NuvioMobile 0.2.20
bin/build-target targets/NuvioMedia__NuvioMobile__70004b7.json
```

The build dispatcher requires a clean `main` exactly matching `origin/main`, a private repository, and authenticated GitHub CLI. Successful artifacts are downloaded under `dist/REQUEST_ID/`, reverified locally, and reported with an absolute path and SHA-256.

## Threat model

Public source builds may execute arbitrary upstream Gradle, Xcode, dependency, or project-generation code. The builder uses ephemeral GitHub-hosted runners, no secrets, read-only tokens, nonpersistent checkout credentials, isolated directories, and a scrubbed child-process environment. This is not a complete sandbox. A separate fresh runner validates the quarantined IPA before publishing the success artifact.

## Output

The Nuvio artifact is named `Nuvio-1.1.20-92-70004b7.unsigned.ipa`. It is not signed and cannot be installed until a separate authorized signing process supplies a valid identity and provisioning profile.

