from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "validate_target", ROOT / "scripts" / "validate_target.py"
)
assert SPEC and SPEC.loader
validate_target = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validate_target)


def valid_manifest() -> dict:
    return json.loads(
        (ROOT / "targets" / "NuvioMedia__NuvioMobile__70004b7.json").read_text()
    )


class TargetValidationTests(unittest.TestCase):
    def assert_invalid(self, mutate, message: str | None = None) -> None:
        data = valid_manifest()
        mutate(data)
        with self.assertRaises(validate_target.ValidationError) as raised:
            validate_target.validate_manifest(data, check_adapter=False)
        if message:
            self.assertIn(message, str(raised.exception))

    def test_nuvio_manifest_is_valid(self) -> None:
        data = valid_manifest()
        result = validate_target.validate_manifest(data, check_adapter=False)
        self.assertEqual(result["runner"], "macos-15-intel")
        self.assertEqual(result["build_environment"], {"NUVIO_IOS_DISTRIBUTION": "full"})

    def test_rejects_unknown_key_at_every_closed_level(self) -> None:
        for path in ((), ("source",), ("container",), ("bootstrap",), ("output",)):
            with self.subTest(path=path):
                def mutation(data, path=path):
                    node = data
                    for component in path:
                        node = node[component]
                    node["typo"] = True
                self.assert_invalid(mutation, "unknown key")

    def test_rejects_missing_required_key(self) -> None:
        self.assert_invalid(lambda d: d["source"].pop("commit"), "missing key")

    def test_rejects_non_integer_schema_version(self) -> None:
        self.assert_invalid(lambda d: d.update(schema_version=True), "integer 1")

    def test_rejects_malformed_repository_names(self) -> None:
        for repository in ("owner", "https://github.com/o/r", "o/r.git", "../r", "o/r/extra"):
            with self.subTest(repository=repository):
                self.assert_invalid(lambda d, v=repository: d["source"].update(repository=v))

    def test_rejects_non_full_commit(self) -> None:
        self.assert_invalid(lambda d: d["source"].update(commit="70004b7"), "40 hexadecimal")

    def test_rejects_dangerous_refs(self) -> None:
        for ref in ("-main", "main\nnext", "main;id", "$(id)", "refs//heads/main", "main..evil", "x@{1}"):
            with self.subTest(ref=ref):
                self.assert_invalid(lambda d, v=ref: d["source"].update(ref=v))

    def test_rejects_path_traversal_and_non_normal_paths(self) -> None:
        for path in ("../LICENSE", "dir/../../LICENSE", "/tmp/LICENSE", "dir\\LICENSE", "dir//LICENSE"):
            with self.subTest(path=path):
                self.assert_invalid(lambda d, v=path: d["source"].update(license_file=v))

    def test_rejects_unsafe_source_patch_contract(self) -> None:
        valid_patch = {
            "path": "patches/fix.patch",
            "sha256": "a" * 64,
            "reason": "Approved upstream compile fix.",
        }
        for path in ("../fix.patch", "patches/nested/fix.patch", "/tmp/fix.patch", "patches/fix.txt"):
            with self.subTest(path=path):
                self.assert_invalid(
                    lambda d, value=path: d.update(source_patch={**valid_patch, "path": value}),
                    "source_patch.path",
                )
        self.assert_invalid(
            lambda d: d.update(source_patch={**valid_patch, "sha256": "A" * 64}),
            "lowercase hexadecimal",
        )
        self.assert_invalid(
            lambda d: d.update(source_patch={**valid_patch, "unexpected": True}),
            "unknown key",
        )

    def test_requires_container_suffix_matching_type(self) -> None:
        self.assert_invalid(
            lambda d: d["container"].update(type="workspace", path="iosApp/iosApp.xcodeproj"),
            ".xcworkspace",
        )

    def test_rejects_unsupported_runner_and_xcode(self) -> None:
        self.assert_invalid(lambda d: d.update(runner="self-hosted"), "must be one of")
        self.assert_invalid(lambda d: d.update(xcode_version="latest"), "must look like")

    def test_accepts_standard_macos_26_runners(self) -> None:
        for runner in ("macos-26", "macos-26-intel"):
            with self.subTest(runner=runner):
                data = valid_manifest()
                data["runner"] = runner
                result = validate_target.validate_manifest(data, check_adapter=False)
                self.assertEqual(result["runner"], runner)

    def test_adapter_is_required_only_for_adapter_mode(self) -> None:
        self.assert_invalid(lambda d: d["bootstrap"].update(adapter=None), "must be a string")
        self.assert_invalid(
            lambda d: d["bootstrap"].update(kind="none"),
            "must be null",
        )

    def test_rejects_nested_or_traversing_adapter(self) -> None:
        for adapter in ("adapters/nested/app.sh", "adapters/../evil.sh", "/tmp/app.sh"):
            with self.subTest(adapter=adapter):
                self.assert_invalid(lambda d, v=adapter: d["bootstrap"].update(adapter=v))

    def test_rejects_reserved_environment(self) -> None:
        for key in (
            "PATH",
            "HOME",
            "SHELL",
            "RUNNER",
            "RUNNER_TEMP",
            "GITHUB_TOKEN",
            "MY_API_KEY",
            "SIGNING_MODE",
            "APPLE_ID",
            "JAVA_HOME",
            "GRADLE_OPTS",
            "KOTLIN_DAEMON_JVMARGS",
        ):
            with self.subTest(key=key):
                self.assert_invalid(lambda d, k=key: d.update(build_environment={k: "x"}))

    def test_rejects_invalid_environment_key_and_control_characters(self) -> None:
        self.assert_invalid(lambda d: d.update(build_environment={"lower": "x"}), "invalid key")
        self.assert_invalid(lambda d: d.update(build_environment={"SAFE_VALUE": "a\nb"}), "NUL or newline")
        self.assert_invalid(lambda d: d.update(build_environment={"SAFE_VALUE": "a\x00b"}), "NUL or newline")

    def test_rejects_signing_overrides(self) -> None:
        for key in ("CODE_SIGNING_ALLOWED", "CODE_SIGN_IDENTITY", "PROVISIONING_PROFILE_SPECIFIER", "DEVELOPMENT_TEAM"):
            with self.subTest(key=key):
                self.assert_invalid(lambda d, k=key: d.update(extra_build_settings={k: "YES"}))

    def test_accepts_non_signing_build_setting(self) -> None:
        data = valid_manifest()
        data["extra_build_settings"] = {"SWIFT_COMPILATION_MODE": "wholemodule"}
        validate_target.validate_manifest(data, check_adapter=False)

    def test_accepts_empty_non_signing_setting_value(self) -> None:
        data = valid_manifest()
        data["extra_build_settings"] = {"SUPPORTED_PLATFORMS": ""}
        validate_target.validate_manifest(data, check_adapter=False)

    def test_rejects_invalid_app_bundle_name(self) -> None:
        for app in ("Payload/Nuvio.app", "Nuvio", ".app", "Nuvio.app\nOTHER"):
            with self.subTest(app=app):
                self.assert_invalid(lambda d, v=app: d["output"].update(expected_app_bundle=v))

    def test_enforces_target_filename(self) -> None:
        data = valid_manifest()
        good = ROOT / "targets" / "NuvioMedia__NuvioMobile__70004b7.json"
        validate_target.validate_manifest(data, manifest_path=good, check_adapter=False)
        versioned = ROOT / "targets" / "NuvioMedia__NuvioMobile__70004b7__xcode26.3.json"
        validate_target.validate_manifest(data, manifest_path=versioned, check_adapter=False)
        with self.assertRaisesRegex(validate_target.ValidationError, "must be named"):
            validate_target.validate_manifest(
                data,
                manifest_path=ROOT / "targets" / "wrong.json",
                check_adapter=False,
            )

    def test_request_id_must_be_canonical_lowercase_uuid(self) -> None:
        request_id = "01234567-89ab-4def-8123-456789abcdef"
        self.assertEqual(validate_target.validate_request_id(request_id), request_id)
        for invalid in ("not-a-uuid", request_id.upper(), "{01234567-89ab-4def-8123-456789abcdef}"):
            with self.subTest(request_id=invalid):
                with self.assertRaises(validate_target.ValidationError):
                    validate_target.validate_request_id(invalid)

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "target.json"
            path.write_text('{"schema_version": 1, "schema_version": 1}')
            with self.assertRaisesRegex(validate_target.ValidationError, "duplicate key"):
                validate_target.load_manifest(path)

    def test_workflow_outputs_are_allowlisted_and_single_line(self) -> None:
        outputs = validate_target.workflow_outputs(valid_manifest())
        self.assertEqual(outputs["runner"], "macos-15-intel")
        self.assertEqual(outputs["bootstrap_adapter"], "adapters/NuvioMedia__NuvioMobile.sh")
        self.assertEqual(outputs["build_environment_json"], '{"NUVIO_IOS_DISTRIBUTION":"full"}')
        self.assertNotIn("source", outputs)
        self.assertTrue(all("\n" not in value for value in outputs.values()))


if __name__ == "__main__":
    unittest.main()
