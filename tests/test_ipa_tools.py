import hashlib
import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "scripts" / "package_ipa.sh"
VERIFY = ROOT / "scripts" / "verify_unsigned_ipa.sh"
REMOVE = ROOT / "scripts" / "remove_signatures.sh"
MANIFEST = ROOT / "scripts" / "write_build_manifest.py"


class IpaToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.work = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def make_app(self, name="Nuvio.app", executable=True):
        app = self.work / name
        app.mkdir()
        plist = {
            "CFBundleExecutable": "Nuvio",
            "CFBundleIdentifier": "com.example.nuvio",
            "CFBundleShortVersionString": "1.1.20",
            "CFBundleVersion": "92",
        }
        with open(app / "Info.plist", "wb") as handle:
            plistlib.dump(plist, handle)
        binary = app / "Nuvio"
        if sys.platform == "darwin" and shutil.which("xcrun"):
            source = self.work / "main.c"
            source.write_text("int main(void) { return 0; }\n")
            result = subprocess.run(
                ["xcrun", "clang", "-target", "arm64-apple-ios13.0", "-c", source, "-o", binary],
                capture_output=True,
                text=True,
            )
            if result.returncode:
                self.skipTest("an iOS-targeting clang is unavailable: " + result.stderr)
        else:
            binary.write_bytes(b"\xcf\xfa\xed\xfe" + b"portable-test-mach-o")
        binary.chmod(0o755 if executable else 0o644)
        return app

    def run_verify(self, ipa, summary=None, env=None):
        command = ["bash", str(VERIFY), str(ipa)]
        if summary:
            command.append(str(summary))
        return subprocess.run(command, capture_output=True, text=True, env=env)

    def fake_tool(self, name, output):
        tools = self.work / "tools"
        tools.mkdir(exist_ok=True)
        tool = tools / name
        tool.write_text("#!/bin/sh\ncat <<'EOF'\n" + output + "\nEOF\n")
        tool.chmod(0o755)
        return tools

    def test_package_and_verify_valid_device_ipa(self):
        app = self.make_app()
        ipa = self.work / "Nuvio.unsigned.ipa"
        subprocess.run(["bash", str(PACKAGE), app, ipa], check=True, capture_output=True, text=True)
        with zipfile.ZipFile(ipa) as archive:
            self.assertIn("Payload/Nuvio.app/Info.plist", archive.namelist())
            self.assertEqual(archive.getinfo("Payload/Nuvio.app/Nuvio").date_time, (1980, 1, 1, 0, 0, 0))
        summary = self.work / "verification.json"
        result = self.run_verify(ipa, summary)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(summary.read_text())
        self.assertEqual(report["bundle_identifier"], "com.example.nuvio")
        self.assertEqual(report["sha256"], hashlib.sha256(ipa.read_bytes()).hexdigest())

    def test_package_requires_unsigned_suffix(self):
        result = subprocess.run(
            ["bash", str(PACKAGE), self.make_app(), self.work / "bad.ipa"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)

    def make_zip(self, entries):
        ipa = self.work / "malicious.unsigned.ipa"
        with zipfile.ZipFile(ipa, "w") as archive:
            for name, contents in entries.items():
                archive.writestr(name, contents)
        return ipa

    def test_rejects_traversal(self):
        result = self.run_verify(self.make_zip({"Payload/../escape": b"x"}))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe", result.stderr)

    def test_rejects_signing_material(self):
        ipa = self.make_zip({
            "Payload/Nuvio.app/Info.plist": plistlib.dumps({"CFBundleExecutable": "Nuvio"}),
            "Payload/Nuvio.app/Nuvio": b"x",
            "Payload/Nuvio.app/_CodeSignature/CodeResources": b"signed",
        })
        result = self.run_verify(ipa)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("signing material", result.stderr)

    def test_rejects_multiple_apps(self):
        plist = plistlib.dumps({"CFBundleExecutable": "Main"})
        ipa = self.make_zip({
            "Payload/One.app/Info.plist": plist,
            "Payload/One.app/Main": b"x",
            "Payload/Two.app/Info.plist": plist,
            "Payload/Two.app/Main": b"x",
        })
        result = self.run_verify(ipa)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exactly one", result.stderr)

    def test_rejects_nested_bundle_with_missing_declared_executable(self):
        app = self.make_app()
        extension = app / "PlugIns" / "DownloadsWidgetExtension.appex"
        extension.mkdir(parents=True)
        with open(extension / "Info.plist", "wb") as handle:
            plistlib.dump({"CFBundleExecutable": "MissingWidget"}, handle)
        ipa = self.work / "nested-missing.unsigned.ipa"
        subprocess.run(["bash", PACKAGE, app, ipa], check=True, capture_output=True, text=True)
        result = self.run_verify(ipa)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("declared bundle executable", result.stderr)

    def test_rejects_undeclared_macho(self):
        app = self.make_app()
        shutil.copy2(app / "Nuvio", app / "RogueHelper")
        ipa = self.work / "rogue.unsigned.ipa"
        subprocess.run(["bash", PACKAGE, app, ipa], check=True, capture_output=True, text=True)
        result = self.run_verify(ipa)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("undeclared Mach-O", result.stderr)

    @unittest.skipUnless(sys.platform == "darwin", "Mach-O platform tools are macOS-only")
    def test_rejects_fat_binary_with_ios_and_simulator_slices(self):
        app = self.make_app()
        ipa = self.work / "mixed-platform.unsigned.ipa"
        subprocess.run(["bash", PACKAGE, app, ipa], check=True, capture_output=True, text=True)
        tools = self.fake_tool("vtool", "platform IOS\nplatform IOSSIMULATOR")
        environment = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ["PATH"])
        result = self.run_verify(ipa, env=environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("simulator Mach-O", result.stderr)

    @unittest.skipUnless(sys.platform == "darwin", "Mach-O load-command tools are macOS-only")
    def test_rejects_residual_code_signature_load_command(self):
        app = self.make_app()
        ipa = self.work / "load-command.unsigned.ipa"
        subprocess.run(["bash", PACKAGE, app, ipa], check=True, capture_output=True, text=True)
        tools = self.fake_tool("otool", "Load command 1\n      cmd LC_CODE_SIGNATURE")
        environment = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ["PATH"])
        result = self.run_verify(ipa, env=environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("LC_CODE_SIGNATURE remains", result.stderr)

    def test_remove_signatures_writes_metadata_and_deletes_residue(self):
        app = self.make_app(executable=False)
        (app / "_CodeSignature").mkdir()
        (app / "_CodeSignature" / "CodeResources").write_text("signature")
        (app / "embedded.mobileprovision").write_text("profile")
        metadata = self.work / "signing.json"
        result = subprocess.run(["bash", str(REMOVE), app, metadata], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(metadata.read_text())
        self.assertEqual(record["bundles"][0]["bundle_identifier"], "com.example.nuvio")
        self.assertFalse((app / "_CodeSignature").exists())
        self.assertFalse((app / "embedded.mobileprovision").exists())

    def test_build_manifest_binds_verified_sha(self):
        ipa = self.work / "Nuvio.unsigned.ipa"
        ipa.write_bytes(b"ipa")
        digest = hashlib.sha256(b"ipa").hexdigest()
        target = self.work / "target.json"
        target.write_text(json.dumps({
            "source": {"repository": "NuvioMedia/NuvioMobile", "ref": "0.2.20", "commit": "7" * 40, "license_spdx": "GPL-3.0-only"},
            "runner": "macos-15-intel",
            "xcode_version": "16.2",
            "configuration": "Release",
            "build_environment": {"NUVIO_IOS_DISTRIBUTION": "full"},
        }))
        verification = self.work / "verification.json"
        verification.write_text(json.dumps({"sha256": digest, "app": "Nuvio.app"}))
        signing = self.work / "signing.json"
        signing.write_text(json.dumps({"format_version": 1, "bundles": []}))
        output = self.work / "build-manifest.json"
        manifest_environment = dict(os.environ, INPUT_REQUEST_ID="00000000-0000-4000-8000-000000000000",
                                    GITHUB_RUN_ID="123")
        subprocess.run([
            sys.executable, MANIFEST, "--target", target, "--ipa", ipa,
            "--verification", verification, "--signing-metadata", signing, "--output", output,
        ], check=True, env=manifest_environment)
        manifest = json.loads(output.read_text())
        self.assertEqual(manifest["artifact"]["sha256"], digest)
        self.assertEqual(manifest["source"]["url"], "https://github.com/NuvioMedia/NuvioMobile")
        self.assertEqual(manifest["build"]["distribution"], "full")
        self.assertEqual(manifest["build"]["request_id"], "00000000-0000-4000-8000-000000000000")
        self.assertEqual(manifest["build"]["github_run_id"], "123")

    def test_build_manifest_rejects_sha_mismatch(self):
        ipa = self.work / "Nuvio.unsigned.ipa"
        ipa.write_bytes(b"ipa")
        target = self.work / "target.json"
        target.write_text(json.dumps({"source": {"repository": "NuvioMedia/NuvioMobile", "commit": "7" * 40}}))
        verification = self.work / "verification.json"
        verification.write_text(json.dumps({"sha256": "0" * 64}))
        signing = self.work / "signing.json"
        signing.write_text("{}")
        result = subprocess.run([
            sys.executable, MANIFEST, "--target", target, "--ipa", ipa,
            "--verification", verification, "--signing-metadata", signing,
            "--output", self.work / "manifest.json",
        ], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256", result.stderr)

    def test_workflow_environment_mode_end_to_end(self):
        app = self.make_app()
        build = self.work / "build"
        archived_app = build / "Nuvio.xcarchive" / "Products" / "Applications" / "Nuvio.app"
        archived_app.parent.mkdir(parents=True)
        shutil.copytree(app, archived_app)
        output = self.work / "output"
        output.mkdir()
        source = self.work / "source"
        source.mkdir()
        (source / "LICENSE").write_text("GPL test fixture\n")
        target = self.work / "target.json"
        target.write_text(json.dumps({
            "source": {
                "repository": "NuvioMedia/NuvioMobile", "ref": "0.2.20",
                "commit": "70004b7b825a8b9fa672a40ec92062884ddf4901",
                "license_spdx": "GPL-3.0-only", "license_file": "LICENSE",
            },
            "runner": "macos-15-intel", "xcode_version": "16.2", "configuration": "Release",
            "build_environment": {"NUVIO_IOS_DISTRIBUTION": "full"},
            "output": {"expected_app_bundle": "Nuvio.app"},
        }))
        environment = dict(os.environ, BUILD_DIR=str(build), OUTPUT_DIR=str(output),
                           TARGET_JSON=str(target), SOURCE_DIR=str(source))
        subprocess.run(["bash", REMOVE], env=environment, check=True, capture_output=True, text=True)
        subprocess.run(["bash", PACKAGE], env=environment, check=True, capture_output=True, text=True)
        ipa = output / "Nuvio-1.1.20-92-70004b7.unsigned.ipa"
        self.assertTrue(ipa.exists())
        self.assertTrue((output / "LICENSE").exists())
        subprocess.run([
            sys.executable, MANIFEST, "--target", target, "--output-dir", output,
            "--request-id", "00000000-0000-4000-8000-000000000000", "--run-id", "123",
        ], check=True)
        pending = json.loads((output / "build-manifest.json").read_text())
        self.assertEqual(pending["status"], "awaiting_fresh_runner_verification")
        verified = self.work / "verified"
        verified.mkdir()
        verify_environment = dict(os.environ, INPUT_IPA=str(ipa), OUTPUT_DIR=str(verified),
                                  TARGET_JSON=str(target))
        result = subprocess.run(["bash", VERIFY], env=verify_environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        final = json.loads((output / "build-manifest.json").read_text())
        self.assertEqual(final["status"], "verified_unsigned")
        self.assertEqual(final["verification"]["sha256"], hashlib.sha256(ipa.read_bytes()).hexdigest())
        local_reverification = self.run_verify(ipa)
        self.assertEqual(local_reverification.returncode, 0, local_reverification.stderr)

        final["artifact"]["sha256"] = "0" * 64
        (output / "build-manifest.json").write_text(json.dumps(final))
        mismatch = subprocess.run(["bash", VERIFY], env=verify_environment, capture_output=True, text=True)
        self.assertNotEqual(mismatch.returncode, 0)
        self.assertIn("manifest artifact SHA-256 mismatch", mismatch.stderr)

        final["artifact"]["sha256"] = hashlib.sha256(ipa.read_bytes()).hexdigest()
        (output / "build-manifest.json").write_text(json.dumps(final))
        changed_target = json.loads(target.read_text())
        changed_target["output"]["expected_app_bundle"] = "Different.app"
        target.write_text(json.dumps(changed_target))
        wrong_app = subprocess.run(["bash", VERIFY], env=verify_environment, capture_output=True, text=True)
        self.assertNotEqual(wrong_app.returncode, 0)
        self.assertIn("expected Different.app", wrong_app.stderr)


if __name__ == "__main__":
    unittest.main()
