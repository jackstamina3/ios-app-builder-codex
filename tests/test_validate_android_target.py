from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "targets" / "android" / "NuvioMedia__NuvioTV__849f702__firetv-stick-4k-max-gen2.json"
SPEC = importlib.util.spec_from_file_location("validate_android_target", ROOT / "scripts" / "validate_android_target.py")
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def valid_manifest():
    return json.loads(TARGET.read_text(encoding="utf-8"))


class AndroidTargetValidationTests(unittest.TestCase):
    def assert_invalid(self, mutate, message=None):
        data = valid_manifest()
        mutate(data)
        with self.assertRaises(validator.ValidationError) as raised:
            validator.validate_manifest(data, check_adapter=False)
        if message:
            self.assertIn(message, str(raised.exception))

    def test_nuviotv_target_is_valid_and_discloses_embedded_version(self):
        data = validator.validate_manifest(valid_manifest(), check_adapter=False)
        self.assertEqual(data["source"]["ref"], "0.7.18")
        self.assertEqual(data["output"]["version_name"], "0.7.17-beta")
        self.assertEqual(data["output"]["abi"], "armeabi-v7a")

    def test_json_schema_accepts_target(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema is not installed")
        schema = json.loads((ROOT / "schemas" / "android-target.schema.json").read_text())
        jsonschema.Draft202012Validator(schema).validate(valid_manifest())

    def test_all_contract_objects_are_closed(self):
        for path in ((), ("source",), ("android_sdk",), ("gradle",), ("bootstrap",), ("output",), ("signing",)):
            with self.subTest(path=path):
                def mutate(data, path=path):
                    node = data
                    for part in path:
                        node = node[part]
                    node["typo"] = True
                self.assert_invalid(mutate, "unknown key")

    def test_rejects_malformed_source_and_refs(self):
        for repository in ("owner", "https://github.com/o/r", "o/r.git", "o/r/extra", "../r"):
            with self.subTest(repository=repository):
                self.assert_invalid(lambda data, value=repository: data["source"].update(repository=value))
        for ref in ("-main", "main..evil", "a@{1}", "main;id", "main\nnext"):
            with self.subTest(ref=ref):
                self.assert_invalid(lambda data, value=ref: data["source"].update(ref=value))
        self.assert_invalid(lambda data: data["source"].update(commit="849f702"), "40 lowercase")

    def test_rejects_traversal_and_unsafe_gradle_tasks(self):
        for path in ("../gradlew", "/tmp/gradlew", "dir//gradlew", "dir\\gradlew"):
            with self.subTest(path=path):
                self.assert_invalid(lambda data, value=path: data["gradle"].update(wrapper=value))
        for task in ("assemble", ":app:assemble --offline", ":app:assemble;id", "$(id)"):
            with self.subTest(task=task):
                self.assert_invalid(lambda data, value=task: data["gradle"].update(task=value))

    def test_rejects_sdk_substitution_and_wrong_profile_abi(self):
        self.assert_invalid(lambda data: data.update(runner="ubuntu-latest"), "ubuntu-24.04")
        self.assert_invalid(lambda data: data.update(java_version="21"), "equal 17")
        self.assert_invalid(lambda data: data["android_sdk"].update(build_tools="latest"), "three-part")
        self.assert_invalid(lambda data: data["android_sdk"].update(ndk="latest"), "exact installed NDK")
        self.assert_invalid(lambda data: data["output"].update(abi="arm64-v8a"), "requires armeabi-v7a")

    def test_rejects_signing_and_reserved_environment(self):
        self.assert_invalid(lambda data: data["signing"].update(mode="release"), "ephemeral test key")
        self.assert_invalid(lambda data: data["signing"].update(minimum_scheme="v1"), "v2-or-newer")
        for key in ("PATH", "JAVA_HOME", "ANDROID_HOME", "CI_USE_DEBUG_SIGNING", "MY_API_KEY", "RELEASE_KEYSTORE"):
            with self.subTest(key=key):
                self.assert_invalid(lambda data, value=key: data.update(build_environment={value: "x"}))

    def test_rejects_unsafe_output_contract(self):
        self.assert_invalid(lambda data: data["output"].update(expected_apk="../app.apk"), "traversal")
        self.assert_invalid(lambda data: data["output"].update(final_name="release.apk"), "test-signed")
        self.assert_invalid(lambda data: data["output"].update(application_id="bad/package"), "application ID")
        self.assert_invalid(lambda data: data["output"].update(version_code=True), "positive")

    def test_enforces_android_target_filename(self):
        validator.validate_manifest(valid_manifest(), manifest_path=TARGET, check_adapter=False)
        with self.assertRaisesRegex(validator.ValidationError, "must be named"):
            validator.validate_manifest(
                valid_manifest(), manifest_path=ROOT / "targets" / "android" / "wrong.json", check_adapter=False
            )

    def test_duplicate_keys_and_request_uuid_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "target.json"
            path.write_text('{"artifact":"android-apk","artifact":"android-apk"}', encoding="utf-8")
            with self.assertRaisesRegex(validator.ValidationError, "duplicate key"):
                validator.load_manifest(path)
        good = "01234567-89ab-4def-8123-456789abcdef"
        self.assertEqual(validator.validate_request_id(good), good)
        with self.assertRaises(validator.ValidationError):
            validator.validate_request_id(good.upper())

    def test_workflow_outputs_are_allowlisted(self):
        outputs = validator.workflow_outputs(valid_manifest())
        self.assertEqual(outputs["gradle_task"], ":app:assembleFullRelease")
        self.assertEqual(outputs["device_profile"], "firetv-stick-4k-max-gen2")
        self.assertEqual(outputs["build_environment_json"], "{}")
        self.assertNotIn("source", outputs)
        self.assertTrue(all("\n" not in value for value in outputs.values()))


if __name__ == "__main__":
    unittest.main()
