#!/usr/bin/env python3
"""Safely verify a Fire TV test-signed APK without extracting it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn


ANDROID = "{http://schemas.android.com/apk/res/android}"
MAX_APK_BYTES = 2 * 1024 * 1024 * 1024
MAX_ENTRIES = 100_000
MAX_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
MAX_SINGLE_ENTRY_BYTES = 1024 * 1024 * 1024
SENSITIVE_SUFFIXES = (
    ".jks", ".keystore", ".p12", ".pfx", ".pk8", ".key", ".mobileprovision",
)
SIGNATURE_SCHEME_RE = re.compile(
    r"Verified using v([234]) scheme(?: \([^)]*\))?:\s*true", re.IGNORECASE
)
CERT_RE = re.compile(r"certificate SHA-256 digest:\s*([0-9a-f:]{64,95})", re.IGNORECASE)


class VerificationError(ValueError):
    """The APK failed a safety, identity, signing, or TV-readiness gate."""


def fail(message: str) -> NoReturn:
    raise VerificationError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member(name: str) -> PurePosixPath:
    if not name or "\x00" in name or "\\" in name or name.startswith("/"):
        fail(f"unsafe APK entry path: {name!r}")
    path = PurePosixPath(name)
    if any(part in {"", ".", ".."} for part in path.parts):
        fail(f"unsafe APK entry path: {name!r}")
    return path


def _elf_identity(header: bytes) -> tuple[int, int] | None:
    if not header.startswith(b"\x7fELF"):
        return None
    if len(header) < 20:
        fail("truncated ELF executable in APK")
    elf_class = header[4]
    byte_order = header[5]
    if byte_order != 1:
        fail("native ELF executable is not little-endian")
    machine = int.from_bytes(header[18:20], "little")
    return elf_class, machine


def portable_checks(apk: Path, target: dict[str, Any]) -> dict[str, Any]:
    if not apk.is_file() or apk.is_symlink():
        fail("APK must be a regular file, not a symlink")
    if apk.suffix.lower() != ".apk":
        fail("artifact must end in .apk")
    size = apk.stat().st_size
    if size <= 0 or size > MAX_APK_BYTES:
        fail("APK is empty or exceeds the 2 GiB limit")
    expected_abi = target["output"]["abi"]
    names: set[str] = set()
    native_libraries: list[str] = []
    total_uncompressed = 0
    try:
        archive = zipfile.ZipFile(apk)
    except (OSError, zipfile.BadZipFile) as exc:
        raise VerificationError(f"APK is not a valid ZIP archive: {exc}") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            fail("APK contains too many entries")
        for info in infos:
            path = _safe_member(info.filename)
            normalized = path.as_posix()
            if normalized in names:
                fail(f"APK contains duplicate entry {normalized}")
            names.add(normalized)
            if info.flag_bits & 0x1:
                fail(f"encrypted APK entry is forbidden: {normalized}")
            file_type = (info.external_attr >> 16) & 0o170000
            if file_type == stat.S_IFLNK:
                fail(f"symlink APK entry is forbidden: {normalized}")
            if info.file_size > MAX_SINGLE_ENTRY_BYTES:
                fail(f"APK entry exceeds the 1 GiB limit: {normalized}")
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
                fail("APK uncompressed size exceeds the 4 GiB limit")
            if info.file_size > 1024 * 1024 and info.compress_size > 0:
                if info.file_size / info.compress_size > 5000:
                    fail(f"suspicious compression ratio for {normalized}")
            lower = normalized.lower()
            if lower.endswith(SENSITIVE_SUFFIXES):
                fail(f"embedded key or provisioning material is forbidden: {normalized}")
            if info.is_dir():
                continue
            with archive.open(info) as member:
                header = member.read(20)
            elf = _elf_identity(header)
            parts = path.parts
            if len(parts) >= 3 and parts[0] == "lib" and lower.endswith(".so"):
                abi = parts[1]
                if abi != expected_abi:
                    fail(f"APK contains unexpected native ABI {abi}; expected only {expected_abi}")
                if elf is None:
                    fail(f"native library is not an ELF executable: {normalized}")
                if expected_abi == "armeabi-v7a" and elf != (1, 40):
                    fail(f"{normalized} is not a 32-bit ARM ELF required by the Fire TV profile")
                native_libraries.append(normalized)
            elif elf is not None:
                fail(f"unexpected ELF executable outside lib/{expected_abi}/: {normalized}")
            elif lower.endswith((".so", ".dylib", ".exe")):
                fail(f"unexpected executable payload: {normalized}")

    if "AndroidManifest.xml" not in names:
        fail("APK is missing AndroidManifest.xml")
    if "classes.dex" not in names:
        fail("APK is missing classes.dex")
    if not native_libraries:
        fail(f"APK has no native libraries for required ABI {expected_abi}")
    return {
        "entry_count": len(names),
        "native_abi": expected_abi,
        "native_libraries": sorted(native_libraries),
        "uncompressed_bytes": total_uncompressed,
    }


def _resolve_tool(explicit: str | None, name: str, sdk: Path | None, candidates: list[str]) -> str:
    if explicit:
        candidate = Path(explicit)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        fail(f"configured {name} is not executable: {explicit}")
    found = shutil.which(name)
    if found:
        return found
    if sdk:
        for relative in candidates:
            candidate = sdk / relative
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    fail(f"{name} is required for full APK verification")


def _run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    rendered = (result.stdout or "") + (result.stderr or "")
    if result.returncode:
        fail(f"verification tool failed ({command[0]}): {rendered.strip()}")
    return rendered


def _manifest_checks(xml_text: str, target: dict[str, Any]) -> dict[str, Any]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise VerificationError(f"apkanalyzer returned malformed manifest XML: {exc}") from exc
    if root.tag != "manifest":
        fail("decoded APK manifest has an unexpected root element")
    output = target["output"]
    application_id = root.attrib.get("package")
    version_name = root.attrib.get(ANDROID + "versionName")
    raw_version_code = root.attrib.get(ANDROID + "versionCode")
    try:
        version_code = int(raw_version_code or "")
    except ValueError:
        fail("decoded APK manifest has an invalid versionCode")
    if application_id != output["application_id"]:
        fail(f"application ID is {application_id!r}, expected {output['application_id']!r}")
    if version_name != output["version_name"] or version_code != output["version_code"]:
        fail(
            f"embedded version is {version_name!r} ({version_code}), expected "
            f"{output['version_name']!r} ({output['version_code']})"
        )

    uses_sdk = root.find("uses-sdk")
    if uses_sdk is None:
        fail("decoded APK manifest is missing uses-sdk")
    try:
        min_sdk = int(uses_sdk.attrib.get(ANDROID + "minSdkVersion", ""))
        target_sdk = int(uses_sdk.attrib.get(ANDROID + "targetSdkVersion", ""))
    except ValueError:
        fail("decoded APK manifest has non-numeric SDK levels")
    if min_sdk > 30:
        fail(f"minSdk {min_sdk} is incompatible with Fire OS 8 / API 30")

    features = {
        node.attrib.get(ANDROID + "name"): node.attrib.get(ANDROID + "required", "true")
        for node in root.findall("uses-feature")
    }
    if features.get("android.hardware.touchscreen") != "false":
        fail("TV-ready manifest must declare touchscreen not required")
    if features.get("android.software.leanback") != "false":
        fail("TV-ready manifest must declare Leanback support as optional")

    application = root.find("application")
    if application is None:
        fail("decoded APK manifest is missing application")
    banner = application.attrib.get(ANDROID + "banner")
    if not banner:
        fail("TV-ready application is missing a banner resource")
    launch_activity = None
    for activity in list(application.findall("activity")) + list(application.findall("activity-alias")):
        for intent_filter in activity.findall("intent-filter"):
            actions = {node.attrib.get(ANDROID + "name") for node in intent_filter.findall("action")}
            categories = {node.attrib.get(ANDROID + "name") for node in intent_filter.findall("category")}
            if "android.intent.action.MAIN" in actions and "android.intent.category.LEANBACK_LAUNCHER" in categories:
                if activity.attrib.get(ANDROID + "exported") != "true":
                    fail("Leanback launcher activity must be exported")
                launch_activity = activity.attrib.get(ANDROID + "name")
    if not launch_activity:
        fail("TV-ready manifest is missing an exported Leanback launcher activity")
    return {
        "application_id": application_id,
        "banner": banner,
        "launch_activity": launch_activity,
        "min_sdk": min_sdk,
        "target_sdk": target_sdk,
        "version_code": version_code,
        "version_name": version_name,
    }


def full_checks(
    apk: Path,
    target: dict[str, Any],
    *,
    apkanalyzer: str | None,
    apksigner: str | None,
    expected_cert_sha256: str | None,
) -> dict[str, Any]:
    sdk_value = os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME")
    sdk = Path(sdk_value) if sdk_value else None
    build_tools = target["android_sdk"]["build_tools"]
    analyzer_tool = _resolve_tool(
        apkanalyzer, "apkanalyzer", sdk,
        ["cmdline-tools/latest/bin/apkanalyzer", "tools/bin/apkanalyzer"],
    )
    signer_tool = _resolve_tool(
        apksigner, "apksigner", sdk,
        [f"build-tools/{build_tools}/apksigner"],
    )
    manifest_text = _run([analyzer_tool, "manifest", "print", str(apk)])
    manifest = _manifest_checks(manifest_text, target)
    signing_text = _run([signer_tool, "verify", "--verbose", "--print-certs", str(apk)])
    schemes = sorted({int(value) for value in SIGNATURE_SCHEME_RE.findall(signing_text)})
    if not any(scheme >= 2 for scheme in schemes):
        fail("APK is not verified with signature scheme v2 or newer")
    certificate = CERT_RE.search(signing_text)
    if not certificate:
        fail("apksigner did not report a signer certificate SHA-256 digest")
    cert_sha = certificate.group(1).replace(":", "").lower()
    if expected_cert_sha256:
        expected = expected_cert_sha256.replace(":", "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            fail("expected certificate SHA-256 is malformed")
        if cert_sha != expected:
            fail("APK signer certificate does not match the builder-created ephemeral key")
    return {"manifest": manifest, "signature_schemes": schemes, "signer_certificate_sha256": cert_sha}


def verify(
    apk: Path,
    target: dict[str, Any],
    *,
    portable_only: bool = False,
    apkanalyzer: str | None = None,
    apksigner: str | None = None,
    expected_cert_sha256: str | None = None,
) -> dict[str, Any]:
    portable = portable_checks(apk, target)
    report: dict[str, Any] = {
        "format_version": 1,
        "artifact": apk.name,
        "bytes": apk.stat().st_size,
        "device_profile": target["device_profile"],
        "portable": portable,
        "sha256": sha256_file(apk),
        "status": "portable_verified" if portable_only else "verified_test_signed_fire_tv_apk",
    }
    if not portable_only:
        report.update(full_checks(
            apk, target, apkanalyzer=apkanalyzer, apksigner=apksigner,
            expected_cert_sha256=expected_cert_sha256,
        ))
    return report


def _write_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--portable-only", action="store_true")
    parser.add_argument("--apkanalyzer")
    parser.add_argument("--apksigner")
    parser.add_argument("--expected-cert-sha256")
    args = parser.parse_args(argv)
    try:
        target = json.loads(args.target.read_text(encoding="utf-8"))
        report = verify(
            args.apk, target, portable_only=args.portable_only, apkanalyzer=args.apkanalyzer,
            apksigner=args.apksigner, expected_cert_sha256=args.expected_cert_sha256,
        )
        if args.output:
            _write_atomic(args.output, report)
    except (OSError, UnicodeError, json.JSONDecodeError, VerificationError) as exc:
        print(f"APK verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
