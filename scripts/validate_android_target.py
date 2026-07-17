#!/usr/bin/env python3
"""Validate a committed Android APK target and emit only allowlisted values."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import subprocess
import sys
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn


REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/+@-]{0,199}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SPDX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+-]{0,99}$")
ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{1,2}$")
NDK_RE = re.compile(r"^[0-9]{1,2}\.[0-9]+\.[0-9]+$")
TASK_RE = re.compile(r"^:[A-Za-z0-9_.-]+(?::[A-Za-z0-9_.-]+)*$")
ADAPTER_RE = re.compile(r"^adapters/[A-Za-z0-9_.-]+$")
PATCH_RE = re.compile(r"^patches/[A-Za-z0-9_.-]+\.patch$")
PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.test-signed\.apk$")
VARIANT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
VERSION_NAME_RE = re.compile(r"^[A-Za-z0-9._+-]+$")

ROOT_KEYS = {
    "schema_version", "artifact", "source", "source_patch", "runner",
    "java_version", "android_sdk", "working_directory", "gradle", "bootstrap",
    "build_environment", "output", "signing", "device_profile",
}
SOURCE_KEYS = {"repository", "ref", "commit", "license_spdx", "license_file"}
PATCH_KEYS = {"path", "sha256", "reason"}
SDK_KEYS = {"compile_sdk", "build_tools", "ndk"}
GRADLE_KEYS = {"wrapper", "distribution_sha256", "task"}
BOOTSTRAP_KEYS = {"kind", "adapter"}
OUTPUT_KEYS = {
    "expected_apk", "final_name", "application_id", "variant", "abi",
    "version_name", "version_code",
}
SIGNING_KEYS = {"mode", "minimum_scheme"}

ABIS = {"armeabi-v7a", "arm64-v8a", "x86", "x86_64"}
RESERVED_ENV_EXACT = {
    "PATH", "HOME", "SHELL", "USER", "LOGNAME", "CI", "TMPDIR", "TMP", "TEMP",
    "LANG", "LC_ALL", "SOURCE_DIR", "BUILD_DIR", "OUTPUT_DIR", "TARGET_JSON",
    "DIAGNOSTICS_DIR", "REQUEST_ID", "JAVA_HOME", "ANDROID_HOME", "ANDROID_SDK_ROOT",
    "ANDROID_NDK_HOME", "NDK_HOME", "GRADLE_USER_HOME", "GRADLE_OPTS",
    "JAVA_TOOL_OPTIONS", "CI_USE_DEBUG_SIGNING", "BUILDER_DIR", "EXPECTED_CERT_SHA256",
    "NUVIO_RELEASE_STORE_FILE", "NUVIO_RELEASE_KEY_ALIAS", "NUVIO_RELEASE_KEY_PASSWORD",
    "NUVIO_RELEASE_STORE_PASSWORD", "SENTRY_AUTH_TOKEN", "GITHUB", "ACTIONS", "RUNNER",
}
RESERVED_ENV_PREFIXES = ("GITHUB_", "ACTIONS_", "RUNNER_", "GH_", "CI_")
RESERVED_ENV_FRAGMENTS = (
    "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "API_KEY", "PRIVATE_KEY",
    "CERTIFICATE", "KEYSTORE", "SIGNING", "STORE_FILE", "KEY_ALIAS", "AUTH_TOKEN",
)


class ValidationError(ValueError):
    """The Android target violated the closed contract."""


def _fail(path: str, message: str) -> NoReturn:
    raise ValidationError(f"{path}: {message}")


def _object(value: Any, path: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    unknown = set(value) - keys
    missing = keys - set(value)
    if unknown:
        _fail(path, f"unknown key(s): {', '.join(sorted(unknown))}")
    if missing:
        _fail(path, f"missing key(s): {', '.join(sorted(missing))}")
    return value


def _string(value: Any, path: str, *, maximum: int = 4096, empty: bool = False) -> str:
    if not isinstance(value, str):
        _fail(path, "must be a string")
    if not empty and not value:
        _fail(path, "must not be empty")
    if len(value) > maximum:
        _fail(path, f"must be at most {maximum} characters")
    if any(character in value for character in ("\x00", "\n", "\r")):
        _fail(path, "must not contain NUL or newline characters")
    return value


def _relative(value: Any, path: str, *, allow_dot: bool = False) -> str:
    raw = _string(value, path, maximum=1024)
    if "\\" in raw:
        _fail(path, "must use POSIX separators")
    parsed = PurePosixPath(raw)
    if parsed.is_absolute() or any(part == ".." for part in parsed.parts):
        _fail(path, "must be a relative path without traversal")
    if raw == ".":
        if not allow_dot:
            _fail(path, "must name a file")
    elif any(part in {"", "."} for part in raw.split("/")):
        _fail(path, "must be normalized")
    return raw


def _safe_ref(value: Any) -> str:
    ref = _string(value, "source.ref", maximum=200)
    if not REF_RE.fullmatch(ref):
        _fail("source.ref", "contains unsupported characters or starts with a dash")
    if ".." in ref or "@{" in ref or "//" in ref or ref.endswith(("/", ".", ".lock")):
        _fail("source.ref", "is not a safe canonical Git ref")
    return ref


def _git_has_head(root: Path) -> bool:
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    ).returncode == 0


def _git_tracks(root: Path, relative: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", relative],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    ).returncode == 0


def validate_request_id(request_id: str) -> str:
    try:
        parsed = uuid.UUID(request_id)
    except (ValueError, AttributeError) as exc:
        raise ValidationError("request_id: must be a UUID") from exc
    if request_id != str(parsed):
        _fail("request_id", "must be a canonical lowercase UUID")
    return request_id


def validate_manifest(
    data: Any,
    *,
    manifest_path: Path | None = None,
    builder_root: Path | None = None,
    check_adapter: bool = True,
) -> dict[str, Any]:
    root = _object(data, "$", ROOT_KEYS)
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        _fail("schema_version", "must equal integer 1")
    if root["artifact"] != "android-apk":
        _fail("artifact", "must equal android-apk")

    source = _object(root["source"], "source", SOURCE_KEYS)
    repository = _string(source["repository"], "source.repository", maximum=201)
    if not REPOSITORY_RE.fullmatch(repository):
        _fail("source.repository", "must use canonical OWNER/REPOSITORY form")
    owner, repo = repository.split("/", 1)
    if owner in {".", ".."} or repo in {".", ".."} or repo.endswith(".git"):
        _fail("source.repository", "must not include .git or traversal")
    _safe_ref(source["ref"])
    commit = _string(source["commit"], "source.commit", maximum=40)
    if not COMMIT_RE.fullmatch(commit):
        _fail("source.commit", "must be exactly 40 lowercase hexadecimal characters")
    if not SPDX_RE.fullmatch(_string(source["license_spdx"], "source.license_spdx", maximum=100)):
        _fail("source.license_spdx", "must be a conservative SPDX identifier")
    _relative(source["license_file"], "source.license_file")

    patch = root["source_patch"]
    if patch is not None:
        patch = _object(patch, "source_patch", PATCH_KEYS)
        patch_path = _relative(patch["path"], "source_patch.path")
        if not PATCH_RE.fullmatch(patch_path):
            _fail("source_patch.path", "must be a direct .patch file under patches/")
        patch_sha = _string(patch["sha256"], "source_patch.sha256", maximum=64)
        if not SHA256_RE.fullmatch(patch_sha):
            _fail("source_patch.sha256", "must be 64 lowercase hexadecimal characters")
        _string(patch["reason"], "source_patch.reason", maximum=500)
        if builder_root is not None:
            patch_file = builder_root / patch_path
            try:
                resolved = patch_file.resolve(strict=True)
            except FileNotFoundError:
                _fail("source_patch.path", "does not exist")
            if patch_file.is_symlink() or not resolved.is_file() or resolved.parent != (builder_root / "patches").resolve():
                _fail("source_patch.path", "must be a regular file directly under patches/")
            if _git_has_head(builder_root) and not _git_tracks(builder_root, patch_path):
                _fail("source_patch.path", "must be committed")
            if hashlib.sha256(resolved.read_bytes()).hexdigest() != patch_sha:
                _fail("source_patch.sha256", "does not match the committed patch")

    if root["runner"] != "ubuntu-24.04":
        _fail("runner", "must equal ubuntu-24.04")
    if root["java_version"] != "17":
        _fail("java_version", "must equal 17")
    sdk = _object(root["android_sdk"], "android_sdk", SDK_KEYS)
    if type(sdk["compile_sdk"]) is not int or not 24 <= sdk["compile_sdk"] <= 99:
        _fail("android_sdk.compile_sdk", "must be an integer from 24 through 99")
    if not VERSION_RE.fullmatch(_string(sdk["build_tools"], "android_sdk.build_tools", maximum=16)):
        _fail("android_sdk.build_tools", "must be an exact three-part version")
    if not NDK_RE.fullmatch(_string(sdk["ndk"], "android_sdk.ndk", maximum=32)):
        _fail("android_sdk.ndk", "must be an exact installed NDK version")
    _relative(root["working_directory"], "working_directory", allow_dot=True)

    gradle = _object(root["gradle"], "gradle", GRADLE_KEYS)
    wrapper = _relative(gradle["wrapper"], "gradle.wrapper")
    if Path(wrapper).name != "gradlew":
        _fail("gradle.wrapper", "must point to a gradlew wrapper")
    distribution_sha = _string(gradle["distribution_sha256"], "gradle.distribution_sha256", maximum=64)
    if not SHA256_RE.fullmatch(distribution_sha):
        _fail("gradle.distribution_sha256", "must be 64 lowercase hexadecimal characters")
    if not TASK_RE.fullmatch(_string(gradle["task"], "gradle.task", maximum=200)):
        _fail("gradle.task", "must be one exact Gradle task without arguments")

    bootstrap = _object(root["bootstrap"], "bootstrap", BOOTSTRAP_KEYS)
    kind = bootstrap["kind"]
    if kind not in {"none", "adapter"}:
        _fail("bootstrap.kind", "must be none or adapter")
    adapter = bootstrap["adapter"]
    if kind == "adapter":
        adapter = _string(adapter, "bootstrap.adapter", maximum=220)
        if not ADAPTER_RE.fullmatch(adapter):
            _fail("bootstrap.adapter", "must be a direct file under adapters/")
    elif adapter is not None:
        _fail("bootstrap.adapter", "must be null when bootstrap.kind is none")

    environment = root["build_environment"]
    if not isinstance(environment, dict):
        _fail("build_environment", "must be an object")
    for key, value in environment.items():
        if not isinstance(key, str) or not ENV_RE.fullmatch(key):
            _fail(f"build_environment.{key}", "has an invalid key")
        _string(value, f"build_environment.{key}", maximum=4096, empty=True)
        if key in RESERVED_ENV_EXACT or key.startswith(RESERVED_ENV_PREFIXES):
            _fail(f"build_environment.{key}", "is controlled by the builder")
        if any(fragment in key for fragment in RESERVED_ENV_FRAGMENTS):
            _fail(f"build_environment.{key}", "may expose credentials or signing material")

    output = _object(root["output"], "output", OUTPUT_KEYS)
    expected_apk = _relative(output["expected_apk"], "output.expected_apk")
    if not expected_apk.endswith(".apk"):
        _fail("output.expected_apk", "must end in .apk")
    if not NAME_RE.fullmatch(_string(output["final_name"], "output.final_name", maximum=255)):
        _fail("output.final_name", "must be a single .test-signed.apk filename")
    if not PACKAGE_RE.fullmatch(_string(output["application_id"], "output.application_id", maximum=255)):
        _fail("output.application_id", "must be a canonical Android application ID")
    if not VARIANT_RE.fullmatch(_string(output["variant"], "output.variant", maximum=100)):
        _fail("output.variant", "must be a single Gradle variant name")
    if output["abi"] not in ABIS:
        _fail("output.abi", f"must be one of: {', '.join(sorted(ABIS))}")
    if not VERSION_NAME_RE.fullmatch(_string(output["version_name"], "output.version_name", maximum=100)):
        _fail("output.version_name", "contains unsupported characters")
    if type(output["version_code"]) is not int or not 1 <= output["version_code"] <= 2_100_000_000:
        _fail("output.version_code", "must be a positive Android version code")

    signing = _object(root["signing"], "signing", SIGNING_KEYS)
    if signing != {"mode": "ephemeral_test", "minimum_scheme": "v2"}:
        _fail("signing", "must require an ephemeral test key and v2-or-newer signatures")
    if root["device_profile"] != "firetv-stick-4k-max-gen2":
        _fail("device_profile", "is not an allowlisted device profile")
    if root["device_profile"] == "firetv-stick-4k-max-gen2" and output["abi"] != "armeabi-v7a":
        _fail("output.abi", "Fire TV Stick 4K Max (2nd generation) requires armeabi-v7a")

    if manifest_path is not None:
        expected = f"{owner}__{repo}__{commit[:7]}__{root['device_profile']}.json"
        if manifest_path.name != expected or manifest_path.parent.name != "android" or manifest_path.parent.parent.name != "targets":
            _fail("target", f"must be named targets/android/{expected}")

    if kind == "adapter" and check_adapter:
        if builder_root is None:
            _fail("bootstrap.adapter", "cannot check adapter without builder root")
        candidate = builder_root / adapter
        try:
            resolved = candidate.resolve(strict=True)
            adapters_root = (builder_root / "adapters").resolve(strict=True)
        except FileNotFoundError:
            _fail("bootstrap.adapter", "does not exist")
        mode = resolved.stat().st_mode
        if candidate.is_symlink() or not resolved.is_file() or resolved.parent != adapters_root:
            _fail("bootstrap.adapter", "must be a regular file directly under adapters/")
        if not mode & stat.S_IXUSR:
            _fail("bootstrap.adapter", "must be executable by its owner")
        if mode & (stat.S_IWGRP | stat.S_IWOTH):
            _fail("bootstrap.adapter", "must not be group- or world-writable")
        if _git_has_head(builder_root) and not _git_tracks(builder_root, adapter):
            _fail("bootstrap.adapter", "must be committed")

    return root


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"JSON: duplicate key {key!r}")
        result[key] = value
    return result


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle, object_pairs_hook=_reject_duplicate_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"target: cannot read valid UTF-8 JSON: {exc}") from exc
    if not isinstance(data, dict):
        _fail("$", "must be an object")
    return data


def workflow_outputs(data: dict[str, Any]) -> dict[str, str]:
    return {
        "runner": data["runner"],
        "source_repository": data["source"]["repository"],
        "source_ref": data["source"]["ref"],
        "source_commit": data["source"]["commit"],
        "license_spdx": data["source"]["license_spdx"],
        "license_file": data["source"]["license_file"],
        "java_version": data["java_version"],
        "compile_sdk": str(data["android_sdk"]["compile_sdk"]),
        "build_tools": data["android_sdk"]["build_tools"],
        "ndk": data["android_sdk"]["ndk"],
        "working_directory": data["working_directory"],
        "gradle_wrapper": data["gradle"]["wrapper"],
        "gradle_distribution_sha256": data["gradle"]["distribution_sha256"],
        "gradle_task": data["gradle"]["task"],
        "bootstrap_kind": data["bootstrap"]["kind"],
        "bootstrap_adapter": data["bootstrap"]["adapter"] or "",
        "build_environment_json": json.dumps(data["build_environment"], sort_keys=True, separators=(",", ":")),
        "expected_apk": data["output"]["expected_apk"],
        "final_name": data["output"]["final_name"],
        "application_id": data["output"]["application_id"],
        "variant": data["output"]["variant"],
        "abi": data["output"]["abi"],
        "version_name": data["output"]["version_name"],
        "version_code": str(data["output"]["version_code"]),
        "device_profile": data["device_profile"],
    }


def write_github_output(path: Path, values: dict[str, str]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                _fail(key, "cannot be a single-line workflow output")
            handle.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    parser.add_argument("--request-id")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--skip-adapter-check", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    target = args.target if args.target.is_absolute() else Path.cwd() / args.target
    try:
        if target.is_symlink():
            _fail("target", "must not be a symlink")
        target = target.resolve(strict=True)
        targets_root = (root / "targets" / "android").resolve(strict=True)
        if target.parent != targets_root or not target.is_file():
            _fail("target", "must be a direct regular file under targets/android/")
        relative = target.relative_to(root).as_posix()
        if _git_has_head(root) and not _git_tracks(root, relative):
            _fail("target", "must be committed to the builder repository")
        data = load_manifest(target)
        validate_manifest(data, manifest_path=target, builder_root=root, check_adapter=not args.skip_adapter_check)
        if args.request_id:
            validate_request_id(args.request_id)
        outputs = workflow_outputs(data)
        if args.github_output:
            write_github_output(args.github_output, outputs)
    except (ValidationError, FileNotFoundError) as exc:
        print(f"Android target validation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(outputs, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
