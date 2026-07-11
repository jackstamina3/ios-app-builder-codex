#!/usr/bin/env python3
"""Write the immutable, verified build record shipped beside an IPA."""

import argparse
import hashlib
import json
import os
import pathlib
import sys


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, help="validated target manifest JSON")
    parser.add_argument("--ipa")
    parser.add_argument("--verification")
    parser.add_argument("--signing-metadata")
    parser.add_argument("--output")
    parser.add_argument("--output-dir", help="workflow mode: discover standard files in this directory")
    parser.add_argument("--request-id")
    parser.add_argument("--run-id")
    parser.add_argument("--submodules", help="optional JSON array of pinned submodules")
    args = parser.parse_args()

    if args.output_dir:
        directory = pathlib.Path(args.output_dir)
        ipas = list(directory.glob("*.unsigned.ipa"))
        if len(ipas) != 1:
            raise SystemExit(f"expected exactly one unsigned IPA in {directory}, found {len(ipas)}")
        args.ipa = str(ipas[0])
        args.output = args.output or str(directory / "build-manifest.json")
        args.signing_metadata = args.signing_metadata or str(directory / "signing-metadata.json")
        source_metadata_path = directory / "source-metadata.json"
        if source_metadata_path.exists() and not args.submodules:
            metadata = load_json(source_metadata_path)
            workflow_submodules = metadata.get("submodules", [])
        else:
            workflow_submodules = []
    else:
        workflow_submodules = []
    missing = [name for name in ("ipa", "signing_metadata", "output") if not getattr(args, name)]
    if missing:
        parser.error("missing required arguments: " + ", ".join("--" + name.replace("_", "-") for name in missing))

    target = load_json(args.target)
    signing_metadata = load_json(args.signing_metadata)
    actual_sha = sha256(args.ipa)
    if args.verification:
        verification = load_json(args.verification)
        if verification.get("sha256") != actual_sha:
            raise SystemExit("verification SHA-256 does not match IPA")
        if "ipa" in verification:
            verification["ipa"] = os.path.basename(verification["ipa"])
        status = "verified_unsigned"
    else:
        verification = None
        status = "awaiting_fresh_runner_verification"
    source = target.get("source")
    if not isinstance(source, dict):
        raise SystemExit("target source object is missing")
    repository, commit = source.get("repository"), source.get("commit")
    if not isinstance(repository, str) or not isinstance(commit, str):
        raise SystemExit("target source repository/commit is missing")

    manifest = {
        "format_version": 1,
        "artifact": {
            "filename": os.path.basename(args.ipa),
            "sha256": actual_sha,
            "bytes": os.path.getsize(args.ipa),
        },
        "source": {
            "repository": repository,
            "url": f"https://github.com/{repository}",
            "ref": source.get("ref"),
            "commit": commit,
            "license_spdx": source.get("license_spdx"),
            "license_file": source.get("license_file"),
            "patch": target.get("source_patch"),
        },
        "build": {
            "runner": target.get("runner"),
            "xcode_version": target.get("xcode_version"),
            "configuration": target.get("configuration"),
            "distribution": target.get("build_environment", {}).get("NUVIO_IOS_DISTRIBUTION"),
            "request_id": args.request_id or os.environ.get("INPUT_REQUEST_ID"),
            "github_run_id": args.run_id or os.environ.get("GITHUB_RUN_ID"),
        },
        "status": status,
        "pre_strip_signing_metadata": signing_metadata,
        "submodules": load_json(args.submodules) if args.submodules else workflow_submodules,
    }
    if verification is not None:
        manifest["verification"] = verification
    if not isinstance(manifest["submodules"], list):
        raise SystemExit("submodules JSON must be an array")
    output = pathlib.Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, output)


if __name__ == "__main__":
    main()
