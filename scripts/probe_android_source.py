#!/usr/bin/env python3
"""Statically inspect pinned Android source without executing its build scripts."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def text(path: Path, limit: int = 2_000_000) -> str:
    if not path.is_file() or path.stat().st_size > limit:
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def block(body: str, marker: str) -> str:
    """Return a conservative brace-delimited block after *marker*."""
    start = body.find(marker)
    if start < 0:
        return ""
    opening = body.find("{", start + len(marker))
    if opening < 0:
        return ""
    depth = 0
    for index in range(opening, len(body)):
        if body[index] == "{":
            depth += 1
        elif body[index] == "}":
            depth -= 1
            if depth == 0:
                return body[opening + 1:index]
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    root = args.source.resolve(strict=True)
    wrappers = sorted(path.relative_to(root).as_posix() for path in root.glob("**/gradlew") if path.is_file())
    manifests = sorted(path.relative_to(root).as_posix() for path in root.glob("**/src/main/AndroidManifest.xml") if path.is_file())
    build_files = sorted(
        path.relative_to(root).as_posix()
        for pattern in ("**/build.gradle", "**/build.gradle.kts")
        for path in root.glob(pattern)
        if path.is_file() and "/build/" not in path.relative_to(root).as_posix()
    )
    applications = []
    for relative in build_files:
        body = text(root / relative)
        plugin_lines = [line for line in body.splitlines() if re.search(r"(?:com\.android\.application|android\.application)", line)]
        if any("apply false" not in line for line in plugin_lines):
            flavors_body = block(body, "productFlavors")
            applications.append({
                "build_file": relative,
                "application_ids": sorted(set(re.findall(r"applicationId\s*[=(]?\s*[\"']([^\"']+)", body))),
                "compile_sdks": sorted(set(re.findall(r"compileSdk\s*[=(]?\s*(\d+)", body))),
                "min_sdks": sorted(set(re.findall(r"minSdk\s*[=(]?\s*(\d+)", body))),
                "target_sdks": sorted(set(re.findall(r"targetSdk\s*[=(]?\s*(\d+)", body))),
                "version_codes": sorted(set(re.findall(r"versionCode\s*[=(]?\s*(\d+)", body))),
                "version_names": sorted(set(re.findall(r"versionName\s*[=(]?\s*[\"']([^\"']+)", body))),
                "flavors": sorted(set(re.findall(r"create\s*\(\s*[\"']([A-Za-z0-9_-]+)[\"']", flavors_body))),
            })
    wrapper_records = []
    for relative in wrappers:
        properties = root / Path(relative).parent / "gradle/wrapper/gradle-wrapper.properties"
        body = text(properties)
        url = next((line.split("=", 1)[1] for line in body.splitlines() if line.startswith("distributionUrl=")), None)
        checksum = next((line.split("=", 1)[1] for line in body.splitlines() if line.startswith("distributionSha256Sum=")), None)
        wrapper_records.append({"path": relative, "distribution_url": url, "distribution_sha256": checksum})
    manifest_records = []
    for relative in manifests:
        body = text(root / relative)
        manifest_records.append({
            "path": relative,
            "leanback_launcher": "android.intent.category.LEANBACK_LAUNCHER" in body,
            "touchscreen_not_required": bool(re.search(r"android\.hardware\.touchscreen[\s\S]{0,160}android:required=\"false\"", body)),
            "application_banner": bool(re.search(r"<application[\s\S]{0,1000}android:banner=", body)),
        })
    licenses = sorted(path.name for path in root.glob("LICENSE*") if path.is_file())
    report = {
        "format_version": 1,
        "inspection": "static_only_source_build_scripts_not_executed",
        "source": {"repository": args.repository, "ref": args.ref, "commit": args.commit},
        "gradle_wrappers": wrapper_records,
        "application_modules": applications,
        "main_manifests": manifest_records,
        "licenses": licenses,
        "requires_human_selection": len(applications) != 1 or any(len(item["flavors"]) > 1 for item in applications),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
