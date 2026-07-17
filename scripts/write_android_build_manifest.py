#!/usr/bin/env python3
"""Write the verified build record shipped beside a test-signed Android APK."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


def load(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--verification", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--submodules", type=Path)
    parser.add_argument("--request-id")
    parser.add_argument("--run-id")
    parser.add_argument("--build-mode", choices=("github_actions", "local_android_sdk"), default="github_actions")
    args = parser.parse_args()

    target = load(args.target)
    verification = load(args.verification)
    apk_sha = digest(args.apk)
    if verification.get("sha256") != apk_sha:
        raise SystemExit("verification SHA-256 does not match APK")
    if verification.get("status") != "verified_test_signed_fire_tv_apk":
        raise SystemExit("full Fire TV APK verification is required")
    output = target["output"]
    manifest = {
        "format_version": 1,
        "artifact": {
            "filename": args.apk.name,
            "bytes": args.apk.stat().st_size,
            "sha256": apk_sha,
            "kind": "test-signed-apk",
            "installable_only_for_testing": True,
        },
        "source": {
            **target["source"],
            "url": f"https://github.com/{target['source']['repository']}",
            "patch": target["source_patch"],
        },
        "build": {
            "mode": args.build_mode,
            "runner": target["runner"] if args.build_mode == "github_actions" else None,
            "java_version": target["java_version"],
            "compile_sdk": target["android_sdk"]["compile_sdk"],
            "build_tools": target["android_sdk"]["build_tools"],
            "ndk": target["android_sdk"]["ndk"],
            "gradle_task": target["gradle"]["task"],
            "variant": output["variant"],
            "request_id": args.request_id or os.environ.get("INPUT_REQUEST_ID"),
            "github_run_id": args.run_id or os.environ.get("GITHUB_RUN_ID"),
        },
        "application": {
            "application_id": output["application_id"],
            "embedded_version_name": verification["manifest"]["version_name"],
            "embedded_version_code": verification["manifest"]["version_code"],
            "source_release_ref": target["source"]["ref"],
            "abi": output["abi"],
            "device_profile": target["device_profile"],
            "version_mismatch_disclosed": target["source"]["ref"] != verification["manifest"]["version_name"],
        },
        "signing": {
            "mode": target["signing"]["mode"],
            "certificate_sha256": verification["signer_certificate_sha256"],
            "schemes": verification["signature_schemes"],
            "persistent_key_used": False,
        },
        "status": "verified_test_signed_fire_tv_apk",
        "verification": verification,
        "submodules": load(args.submodules) if args.submodules else [],
    }
    if not isinstance(manifest["submodules"], list):
        raise SystemExit("submodules JSON must be an array")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
