#!/usr/bin/env python3
"""Validate a committed unsigned-IPA target and emit allowlisted values.

The implementation is intentionally dependency-free.  The JSON Schema is the
public contract; this module mirrors it and adds checks that JSON Schema cannot
perform safely, such as repository-relative path and adapter checks.
"""

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
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/+@-]{0,199}$")
SPDX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+-]{0,99}$")
XCODE_RE = re.compile(r"^[0-9]{1,2}\.[0-9]{1,2}(?:\.[0-9]{1,2})?$")
SETTING_RE = re.compile(r"^[A-Z0-9_]+$")
ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
ADAPTER_RE = re.compile(r"^adapters/[A-Za-z0-9_.-]+$")
PATCH_RE = re.compile(r"^patches/[A-Za-z0-9_.-]+\.patch$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
APP_RE = re.compile(r"^[^/\\]+\.app$")

RUNNERS = {"macos-15", "macos-15-intel"}
CONTAINER_TYPES = {"project", "workspace"}
CONFIGURATIONS = {"Release", "Debug"}
BUILD_ACTIONS = {"archive", "build"}
BOOTSTRAP_KINDS = {"none", "swiftpm", "cocoapods", "carthage", "adapter"}

ROOT_KEYS = {
    "schema_version", "source", "source_patch", "runner", "xcode_version",
    "working_directory", "container", "scheme", "configuration",
    "build_action", "bootstrap", "build_environment",
    "extra_build_settings", "output",
}
SOURCE_KEYS = {"repository", "ref", "commit", "license_spdx", "license_file"}
PATCH_KEYS = {"path", "sha256", "reason"}
CONTAINER_KEYS = {"type", "path"}
BOOTSTRAP_KEYS = {"kind", "adapter"}
OUTPUT_KEYS = {"expected_app_bundle"}

SIGNING_SETTINGS = {
    "CODE_SIGNING_ALLOWED", "CODE_SIGNING_REQUIRED", "CODE_SIGN_IDENTITY",
    "EXPANDED_CODE_SIGN_IDENTITY", "DEVELOPMENT_TEAM",
    "PROVISIONING_PROFILE", "PROVISIONING_PROFILE_SPECIFIER",
    "OTHER_CODE_SIGN_FLAGS", "CODE_SIGN_INJECT_BASE_ENTITLEMENTS",
}
RESERVED_ENV_EXACT = {
    "PATH", "HOME", "SHELL", "USER", "LOGNAME", "CI", "TMPDIR", "TMP",
    "TEMP", "LANG", "LC_ALL", "DEVELOPER_DIR", "SOURCE_DIR", "BUILD_DIR",
    "OUTPUT_DIR", "TARGET_JSON", "RUNNER", "GITHUB", "ACTIONS", "GH",
    "APPLE_ID", "KEYCHAIN_PATH", "JAVA_HOME", "GRADLE_USER_HOME", "GRADLE_OPTS",
    "KOTLIN_DAEMON_JVMARGS",
}
RESERVED_ENV_PREFIXES = (
    "GITHUB_", "ACTIONS_", "RUNNER_", "GH_", "CI_",
)
RESERVED_ENV_FRAGMENTS = (
    "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "API_KEY",
    "PRIVATE_KEY", "CERTIFICATE", "PROVISIONING", "CODE_SIGN", "SIGNING",
    "DEVELOPMENT_TEAM",
)


class ValidationError(ValueError):
    """A target manifest violated the contract."""


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


def _string(value: Any, path: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str):
        _fail(path, "must be a string")
    if not value:
        _fail(path, "must not be empty")
    if len(value) > maximum:
        _fail(path, f"must be at most {maximum} characters")
    if "\x00" in value or "\n" in value or "\r" in value:
        _fail(path, "must not contain NUL or newline characters")
    return value


def _map_value(value: Any, path: str, *, maximum: int = 4096) -> str:
    """Validate a map value; empty strings are legitimate build values."""
    if not isinstance(value, str):
        _fail(path, "must be a string")
    if len(value) > maximum:
        _fail(path, f"must be at most {maximum} characters")
    if "\x00" in value or "\n" in value or "\r" in value:
        _fail(path, "must not contain NUL or newline characters")
    return value


def _relative_path(value: Any, path: str, *, allow_dot: bool = False) -> str:
    raw = _string(value, path, maximum=1024)
    if "\\" in raw:
        _fail(path, "must use POSIX path separators")
    parsed = PurePosixPath(raw)
    if parsed.is_absolute() or any(part == ".." for part in parsed.parts):
        _fail(path, "must be a relative path without traversal")
    if raw != "." and any(part in {"", "."} for part in raw.split("/")):
        _fail(path, "must be a normalized relative path")
    if raw == "." and not allow_dot:
        _fail(path, "must name a file or directory")
    return raw


def _scalar_map(value: Any, path: str, key_re: re.Pattern[str]) -> dict[str, str]:
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key_re.fullmatch(key):
            _fail(f"{path}.{key}", "has an invalid key")
        result[key] = _map_value(item, f"{path}.{key}")
    return result


def _validate_ref(value: Any) -> str:
    ref = _string(value, "source.ref", maximum=200)
    if not REF_RE.fullmatch(ref):
        _fail("source.ref", "contains unsupported characters or starts with a dash")
    if ".." in ref or "@{" in ref or "//" in ref or ref.endswith(("/", ".", ".lock")):
        _fail("source.ref", "is not a safe canonical Git ref")
    return ref


def validate_request_id(request_id: str) -> str:
    try:
        parsed = uuid.UUID(request_id)
    except (ValueError, AttributeError) as exc:
        raise ValidationError("request_id: must be a UUID") from exc
    canonical = str(parsed)
    if request_id != canonical:
        _fail("request_id", "must be a canonical lowercase UUID")
    return canonical


def _git_has_head(root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    return result.returncode == 0


def _git_tracks(root: Path, relative: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", relative],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    return result.returncode == 0


def validate_manifest(
    data: Any,
    *,
    manifest_path: Path | None = None,
    builder_root: Path | None = None,
    check_adapter: bool = True,
) -> dict[str, Any]:
    """Validate and return *data*. Raises ValidationError on the first error."""
    root = _object(data, "$", ROOT_KEYS)
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        _fail("schema_version", "must equal integer 1")

    source = _object(root["source"], "source", SOURCE_KEYS)
    repository = _string(source["repository"], "source.repository", maximum=201)
    if not REPOSITORY_RE.fullmatch(repository):
        _fail("source.repository", "must use canonical OWNER/REPOSITORY form")
    owner, repo = repository.split("/", 1)
    if owner in {".", ".."} or repo in {".", ".."} or repo.endswith(".git"):
        _fail("source.repository", "must use canonical OWNER/REPOSITORY form without .git")
    _validate_ref(source["ref"])
    commit = _string(source["commit"], "source.commit", maximum=40)
    if not COMMIT_RE.fullmatch(commit):
        _fail("source.commit", "must be exactly 40 hexadecimal characters")
    spdx = _string(source["license_spdx"], "source.license_spdx", maximum=100)
    if not SPDX_RE.fullmatch(spdx):
        _fail("source.license_spdx", "must be a conservative SPDX identifier")
    _relative_path(source["license_file"], "source.license_file")

    source_patch = root["source_patch"]
    if source_patch is not None:
        patch = _object(source_patch, "source_patch", PATCH_KEYS)
        patch_path = _relative_path(patch["path"], "source_patch.path")
        if not PATCH_RE.fullmatch(patch_path):
            _fail("source_patch.path", "must be a direct .patch file under patches/")
        patch_sha = _string(patch["sha256"], "source_patch.sha256", maximum=64)
        if not SHA256_RE.fullmatch(patch_sha):
            _fail("source_patch.sha256", "must be 64 lowercase hexadecimal characters")
        _string(patch["reason"], "source_patch.reason", maximum=500)
        if builder_root is not None:
            patch_file = builder_root / patch_path
            try:
                if patch_file.is_symlink() or not patch_file.resolve(strict=True).is_file():
                    _fail("source_patch.path", "must resolve to a committed regular file")
            except FileNotFoundError:
                _fail("source_patch.path", "does not exist")
            if patch_file.resolve().parent != (builder_root / "patches").resolve():
                _fail("source_patch.path", "must resolve directly under patches/")
            if _git_has_head(builder_root) and not _git_tracks(builder_root, patch_path):
                _fail("source_patch.path", "must be committed to the builder repository")
            actual_patch_sha = hashlib.sha256(patch_file.read_bytes()).hexdigest()
            if actual_patch_sha != patch_sha:
                _fail("source_patch.sha256", "does not match the committed patch file")

    runner = _string(root["runner"], "runner")
    if runner not in RUNNERS:
        _fail("runner", f"must be one of: {', '.join(sorted(RUNNERS))}")
    xcode = _string(root["xcode_version"], "xcode_version")
    if not XCODE_RE.fullmatch(xcode):
        _fail("xcode_version", "must look like 16.2 or 16.2.1")
    _relative_path(root["working_directory"], "working_directory", allow_dot=True)

    container = _object(root["container"], "container", CONTAINER_KEYS)
    container_type = _string(container["type"], "container.type")
    if container_type not in CONTAINER_TYPES:
        _fail("container.type", "must be project or workspace")
    container_path = _relative_path(container["path"], "container.path")
    suffix = ".xcodeproj" if container_type == "project" else ".xcworkspace"
    if not container_path.endswith(suffix):
        _fail("container.path", f"must end in {suffix} for type {container_type}")

    _string(root["scheme"], "scheme", maximum=200)
    configuration = _string(root["configuration"], "configuration")
    if configuration not in CONFIGURATIONS:
        _fail("configuration", "must be Release or Debug")
    action = _string(root["build_action"], "build_action")
    if action not in BUILD_ACTIONS:
        _fail("build_action", "must be archive or build")

    bootstrap = _object(root["bootstrap"], "bootstrap", BOOTSTRAP_KEYS)
    kind = _string(bootstrap["kind"], "bootstrap.kind")
    if kind not in BOOTSTRAP_KINDS:
        _fail("bootstrap.kind", "is not supported")
    adapter = bootstrap["adapter"]
    if kind == "adapter":
        adapter = _string(adapter, "bootstrap.adapter", maximum=220)
        if not ADAPTER_RE.fullmatch(adapter):
            _fail("bootstrap.adapter", "must be a direct executable file under adapters/")
    elif adapter is not None:
        _fail("bootstrap.adapter", "must be null unless bootstrap.kind is adapter")

    build_env = _scalar_map(root["build_environment"], "build_environment", ENV_RE)
    for key in build_env:
        if key in RESERVED_ENV_EXACT or key.startswith(RESERVED_ENV_PREFIXES):
            _fail(f"build_environment.{key}", "is controlled by the builder")
        if any(fragment in key for fragment in RESERVED_ENV_FRAGMENTS):
            _fail(f"build_environment.{key}", "may expose credentials or signing material")

    settings = _scalar_map(root["extra_build_settings"], "extra_build_settings", SETTING_RE)
    for key in settings:
        if key in SIGNING_SETTINGS or key.startswith(("CODE_SIGN", "PROVISIONING_PROFILE")):
            _fail(f"extra_build_settings.{key}", "cannot override mandatory no-sign settings")

    output = _object(root["output"], "output", OUTPUT_KEYS)
    app = _string(output["expected_app_bundle"], "output.expected_app_bundle", maximum=255)
    if not APP_RE.fullmatch(app) or app in {".app", "..app"}:
        _fail("output.expected_app_bundle", "must be a single .app bundle name")

    if manifest_path is not None:
        expected_name = f"{owner}__{repo}__{commit[:7].lower()}.json"
        if manifest_path.name != expected_name or manifest_path.parent.name != "targets":
            _fail("target", f"must be named targets/{expected_name}")

    if kind == "adapter" and check_adapter:
        if builder_root is None:
            _fail("bootstrap.adapter", "cannot check adapter without the builder root")
        adapter_file = builder_root / adapter
        try:
            if adapter_file.is_symlink():
                _fail("bootstrap.adapter", "must not be a symlink")
            resolved = adapter_file.resolve(strict=True)
            adapters_root = (builder_root / "adapters").resolve(strict=True)
        except FileNotFoundError:
            _fail("bootstrap.adapter", "does not exist")
        if resolved.parent != adapters_root or not resolved.is_file():
            _fail("bootstrap.adapter", "must resolve to a regular file directly under adapters/")
        if not (resolved.stat().st_mode & stat.S_IXUSR):
            _fail("bootstrap.adapter", "must be executable by its owner")
        if _git_has_head(builder_root) and not _git_tracks(builder_root, adapter):
            _fail("bootstrap.adapter", "must be committed to the builder repository")

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
    """Return the complete allowlist exposed by the plan job."""
    return {
        "runner": data["runner"],
        "source_repository": data["source"]["repository"],
        "source_ref": data["source"]["ref"],
        "source_commit": data["source"]["commit"].lower(),
        "license_spdx": data["source"]["license_spdx"],
        "license_file": data["source"]["license_file"],
        "xcode_version": data["xcode_version"],
        "working_directory": data["working_directory"],
        "container_type": data["container"]["type"],
        "container_path": data["container"]["path"],
        "scheme": data["scheme"],
        "configuration": data["configuration"],
        "build_action": data["build_action"],
        "bootstrap_kind": data["bootstrap"]["kind"],
        "bootstrap_adapter": data["bootstrap"]["adapter"] or "",
        "build_environment_json": json.dumps(data["build_environment"], sort_keys=True, separators=(",", ":")),
        "extra_build_settings_json": json.dumps(data["extra_build_settings"], sort_keys=True, separators=(",", ":")),
        "expected_app_bundle": data["output"]["expected_app_bundle"],
    }


def write_github_output(path: Path, values: dict[str, str]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                _fail(key, "cannot be emitted as a single-line workflow output")
            handle.write(f"{key}={value}\n")


def _builder_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="committed manifest under targets/")
    parser.add_argument("--request-id", help="also validate a canonical workflow request UUID")
    parser.add_argument("--github-output", type=Path, help="append validated outputs to this file")
    parser.add_argument("--skip-adapter-check", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    root = _builder_root()
    target = args.target if args.target.is_absolute() else Path.cwd() / args.target
    try:
        if target.is_symlink():
            _fail("target", "must be a regular file, not a symlink")
        target = target.resolve(strict=True)
        targets_root = (root / "targets").resolve(strict=True)
        if target.parent != targets_root:
            _fail("target", "must be a direct file under targets/")
        if not target.is_file():
            _fail("target", "must be a regular file, not a symlink")
        relative_target = target.relative_to(root).as_posix()
        if _git_has_head(root) and not _git_tracks(root, relative_target):
            _fail("target", "must be committed to the builder repository")
        data = load_manifest(target)
        validate_manifest(
            data,
            manifest_path=target,
            builder_root=root,
            check_adapter=not args.skip_adapter_check,
        )
        if args.request_id is not None:
            validate_request_id(args.request_id)
        outputs = workflow_outputs(data)
        if args.github_output:
            write_github_output(args.github_output, outputs)
    except (ValidationError, FileNotFoundError) as exc:
        print(f"target validation failed: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(outputs, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
