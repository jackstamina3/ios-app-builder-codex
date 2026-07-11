# Unsigned iOS Builder Guidance

This private repository reproducibly builds unsigned iOS IPAs from authorized public source. Never download, decrypt, patch, or repackage an App Store or other proprietary IPA.

## Required operating procedure

1. Confirm an explicit source license or the user's authorization.
2. Resolve every source ref to a full commit SHA and use a committed target under `targets/`.
3. Run `python3 scripts/validate_target.py TARGET` before dispatch.
4. Probe uncertain projects with `bin/probe-source OWNER/REPOSITORY REF`; never guess among schemes or containers.
5. Build with `bin/build-target TARGET`. Inspect diagnostics and make only evidence-based manifest or adapter changes.
6. Report source ref and SHA, license, runner, Xcode, embedded app version, bundle ID, absolute IPA path, SHA-256, and that the result is unsigned.

## Security invariants

- Workflows are manual-only, read-only, secret-free, and use GitHub-hosted runners plus full-SHA-pinned GitHub-owned actions.
- Never add signing identities, profiles, Apple credentials, private dependencies, shared dependency caches, provisioning updates, free-form workflow commands, `sudo`, or unverified tool downloads.
- Build settings cannot override mandatory no-sign values. Do not claim an unsigned IPA is installable without downstream signing.
- Environment scrubbing is defense in depth, not a sandbox. Third-party build code remains untrusted; final verification therefore runs on a fresh runner.
- Stop on binary-only source, missing authorization, unavailable pinned source/Xcode, private dependencies, ambiguous schemes, simulator-only output, or residual signing material.

## Verification

Run `python3 -m unittest discover -s tests`, `bash -n` on shell files, Python compilation, and `python3 tests/test_workflow_policy.py` after changes.

