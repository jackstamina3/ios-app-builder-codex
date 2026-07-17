from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import warnings
from unittest import mock
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "targets" / "android" / "NuvioMedia__NuvioTV__849f702__firetv-stick-4k-max-gen2.json"
VERIFY = ROOT / "scripts" / "verify_android_apk.py"
MANIFEST_WRITER = ROOT / "scripts" / "write_android_build_manifest.py"
SPEC = importlib.util.spec_from_file_location("verify_android_apk", VERIFY)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)

CERT = "ab" * 32


def arm32_elf():
    header = bytearray(20)
    header[:4] = b"\x7fELF"
    header[4] = 1
    header[5] = 1
    header[18:20] = (40).to_bytes(2, "little")
    return bytes(header) + b"fixture"


def arm64_elf():
    header = bytearray(20)
    header[:4] = b"\x7fELF"
    header[4] = 2
    header[5] = 1
    header[18:20] = (183).to_bytes(2, "little")
    return bytes(header) + b"fixture"


class AndroidApkToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.work = Path(self.temp.name)
        self.target = json.loads(TARGET.read_text())

    def tearDown(self):
        self.temp.cleanup()

    def make_apk(self, entries=None):
        apk = self.work / self.target["output"]["final_name"]
        payload = {
            "AndroidManifest.xml": b"binary manifest fixture",
            "classes.dex": b"dex\n035\x00fixture",
            "lib/armeabi-v7a/libfixture.so": arm32_elf(),
        }
        if entries:
            payload.update(entries)
        with zipfile.ZipFile(apk, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, value in payload.items():
                archive.writestr(name, value)
        return apk

    def fake_tools(self, manifest=None, signer=None):
        tools = self.work / "tools"
        tools.mkdir(exist_ok=True)
        manifest = manifest or f'''<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.nuvio.tv" android:versionName="0.7.17-beta" android:versionCode="1035">
<uses-sdk android:minSdkVersion="24" android:targetSdkVersion="36" />
<uses-feature android:name="android.hardware.touchscreen" android:required="false" />
<uses-feature android:name="android.software.leanback" android:required="false" />
<application android:banner="@mipmap/banner"><activity android:name=".MainActivity" android:exported="true"><intent-filter><action android:name="android.intent.action.MAIN"/><category android:name="android.intent.category.LEANBACK_LAUNCHER"/></intent-filter></activity></application>
</manifest>'''
        signer = signer or (
            "Verified using v1 scheme (JAR signing): true\n"
            "Verified using v2 scheme (APK Signature Scheme v2): true\n"
            f"Signer #1 certificate SHA-256 digest: {CERT}\n"
        )
        for name, output in (("apkanalyzer", manifest), ("apksigner", signer)):
            path = tools / name
            path.write_text("#!/bin/sh\ncat <<'EOF'\n" + output + "\nEOF\n", encoding="utf-8")
            path.chmod(0o755)
        return tools / "apkanalyzer", tools / "apksigner"

    def test_valid_fire_tv_apk_passes_full_verification(self):
        apk = self.make_apk()
        analyzer, signer = self.fake_tools()
        report = verifier.verify(
            apk, self.target, apkanalyzer=str(analyzer), apksigner=str(signer), expected_cert_sha256=CERT
        )
        self.assertEqual(report["status"], "verified_test_signed_fire_tv_apk")
        self.assertEqual(report["manifest"]["application_id"], "com.nuvio.tv")
        self.assertEqual(report["portable"]["native_abi"], "armeabi-v7a")
        self.assertEqual(report["signer_certificate_sha256"], CERT)

    def test_portable_mode_needs_no_android_sdk_tools(self):
        report = verifier.verify(self.make_apk(), self.target, portable_only=True)
        self.assertEqual(report["status"], "portable_verified")

    def test_rejects_traversal_duplicate_and_embedded_key(self):
        for bad_name in ("../escape", "/absolute", "dir\\escape", "keys/release.jks"):
            with self.subTest(name=bad_name):
                apk = self.make_apk({bad_name: b"bad"})
                with self.assertRaises(verifier.VerificationError):
                    verifier.portable_checks(apk, self.target)
        duplicate = self.work / "duplicate.apk"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(duplicate, "w") as archive:
                archive.writestr("AndroidManifest.xml", b"one")
                archive.writestr("AndroidManifest.xml", b"two")
                archive.writestr("classes.dex", b"dex")
                archive.writestr("lib/armeabi-v7a/libfixture.so", arm32_elf())
        with self.assertRaisesRegex(verifier.VerificationError, "duplicate"):
            verifier.portable_checks(duplicate, self.target)

    def test_rejects_oversized_apk_and_archive_bomb_budget(self):
        apk = self.make_apk()
        with mock.patch.object(verifier, "MAX_APK_BYTES", apk.stat().st_size - 1):
            with self.assertRaisesRegex(verifier.VerificationError, "2 GiB"):
                verifier.portable_checks(apk, self.target)
        with mock.patch.object(verifier, "MAX_UNCOMPRESSED_BYTES", 10):
            with self.assertRaisesRegex(verifier.VerificationError, "4 GiB"):
                verifier.portable_checks(apk, self.target)

    def test_rejects_wrong_abi_64_bit_arm_and_unexpected_elf(self):
        for entries in (
            {"lib/arm64-v8a/libbad.so": arm64_elf()},
            {"lib/armeabi-v7a/libfixture.so": arm64_elf()},
            {"assets/hidden.bin": arm32_elf()},
        ):
            with self.subTest(entries=tuple(entries)):
                with self.assertRaises(verifier.VerificationError):
                    verifier.portable_checks(self.make_apk(entries), self.target)

    def test_rejects_missing_tv_manifest_requirements_and_malformed_xml(self):
        apk = self.make_apk()
        for manifest in (
            "not xml",
            '''<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.nuvio.tv" android:versionName="0.7.17-beta" android:versionCode="1035"><uses-sdk android:minSdkVersion="31" android:targetSdkVersion="36"/><application/></manifest>''',
        ):
            with self.subTest(manifest=manifest[:20]):
                analyzer, signer = self.fake_tools(manifest=manifest)
                with self.assertRaises(verifier.VerificationError):
                    verifier.verify(apk, self.target, apkanalyzer=str(analyzer), apksigner=str(signer))

    def test_rejects_v1_only_wrong_certificate_and_version_mismatch(self):
        apk = self.make_apk()
        analyzer, signer = self.fake_tools(signer=f"Verified using v1 scheme (JAR signing): true\nSigner #1 certificate SHA-256 digest: {CERT}")
        with self.assertRaisesRegex(verifier.VerificationError, "v2"):
            verifier.verify(apk, self.target, apkanalyzer=str(analyzer), apksigner=str(signer))
        analyzer, signer = self.fake_tools()
        with self.assertRaisesRegex(verifier.VerificationError, "ephemeral"):
            verifier.verify(apk, self.target, apkanalyzer=str(analyzer), apksigner=str(signer), expected_cert_sha256="cd" * 32)
        wrong_version = self.fake_tools()[0]
        wrong_version.write_text(wrong_version.read_text().replace('android:versionName="0.7.17-beta"', 'android:versionName="9.9"'))
        with self.assertRaisesRegex(verifier.VerificationError, "embedded version"):
            verifier.verify(apk, self.target, apkanalyzer=str(wrong_version), apksigner=str(signer))

    def test_build_manifest_binds_apk_sha_and_version_mismatch(self):
        apk = self.make_apk()
        analyzer, signer = self.fake_tools()
        report = verifier.verify(apk, self.target, apkanalyzer=str(analyzer), apksigner=str(signer))
        verification = self.work / "verification.json"
        verification.write_text(json.dumps(report))
        submodules = self.work / "submodules.json"
        submodules.write_text("[]")
        output = self.work / "build-manifest.json"
        subprocess.run([
            sys.executable, MANIFEST_WRITER, "--target", TARGET, "--apk", apk,
            "--verification", verification, "--submodules", submodules,
            "--request-id", "01234567-89ab-4def-8123-456789abcdef",
            "--build-mode", "local_android_sdk", "--output", output,
        ], check=True)
        manifest = json.loads(output.read_text())
        self.assertEqual(manifest["artifact"]["sha256"], hashlib.sha256(apk.read_bytes()).hexdigest())
        self.assertTrue(manifest["application"]["version_mismatch_disclosed"])
        self.assertFalse(manifest["signing"]["persistent_key_used"])


if __name__ == "__main__":
    unittest.main()
