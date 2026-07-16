#!/usr/bin/env python3
"""Validate initialized submodules and write their immutable license records."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess


GITHUB_URL = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?")
MPVKIT_LICENSE = {
    "213e61e14ba3c0413a608e182618a51bd53cc4a9",
    "d5cf091c80368bbbc1bbf2d195fbc55d926df888",
}


def run(*args: str, cwd: pathlib.Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=check, capture_output=True, text=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()
    root = args.source.resolve(strict=True)
    modules = root / ".gitmodules"
    declared: dict[str, str] = {}
    if modules.exists():
        result = run("git", "-C", str(root), "config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$", check=False)
        if result.returncode not in (0, 1):
            raise SystemExit("cannot parse .gitmodules")
        for line in result.stdout.splitlines():
            key, path = line.split(None, 1)
            url_key = key[:-5] + ".url"
            url = run("git", "-C", str(root), "config", "-f", ".gitmodules", "--get", url_key).stdout.strip()
            if not GITHUB_URL.fullmatch(url):
                raise SystemExit(f"unsupported submodule URL: {url!r}")
            declared[path] = url

    records = []
    for line in run("git", "-C", str(root), "ls-files", "-s").stdout.splitlines():
        fields = line.split(None, 3)
        if len(fields) != 4 or fields[0] != "160000":
            continue
        expected, path = fields[1], fields[3]
        record: dict[str, object] = {"commit": expected, "path": path}
        if path not in declared:
            record["status"] = "ignored_unmapped_gitlink"
            records.append(record)
            continue
        child = (root / path).resolve(strict=True)
        child.relative_to(root)
        actual = run("git", "-C", str(child), "rev-parse", "HEAD").stdout.strip()
        if actual != expected:
            raise SystemExit(f"submodule commit mismatch: {path}")
        license_files = sorted(str(item.relative_to(child)) for item in child.glob("LICENSE*") if item.is_file())
        if license_files:
            basis = license_files[0]
            license_sha256 = None
        elif path == "MPVKit" and declared[path] == "https://github.com/NuvioMedia/MPVKit.git" and expected in MPVKIT_LICENSE:
            basis = "repository-level LGPL-3.0 license pinned at ca111517f60e4631fd0b9a3fd0d03689e9f38b8a (not present in source commit)"
            license_sha256 = "ea8af5e789cb2d4e9b10bce3874982ade163b749b6bfbdb32e2df21c4d106de1"
        else:
            readme = child / "README.md"
            text = readme.read_text(encoding="utf-8", errors="replace").lower() if readme.is_file() else ""
            markers = ("gpl", "general public license", "mit license", "apache license", "bsd license", "mozilla public license")
            if not any(marker in text for marker in markers):
                raise SystemExit(f"submodule has no explicit license basis: {path}")
            basis = "README.md"
            license_sha256 = None
        record.update({"status": "initialized", "url": declared[path], "license_basis": basis})
        if license_sha256:
            record["license_sha256"] = license_sha256
        records.append(record)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
