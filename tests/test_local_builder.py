from __future__ import annotations

import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCAL = ROOT / "bin" / "build-local"
ANDROID_LOCAL = ROOT / "bin" / "build-apk-local"
FIRETV_INSTALL = ROOT / "bin" / "install-firetv"
SUBMODULES = ROOT / "scripts" / "record_submodules.py"


class LocalBuilderTests(unittest.TestCase):
    def test_dispatcher_is_executable_and_preserves_security_controls(self) -> None:
        mode = LOCAL.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR)
        text = LOCAL.read_text(encoding="utf-8")
        for required in (
            "scripts/validate_target.py",
            "Working tree must be clean",
            "Target requires Xcode",
            "Target requires Xcode build",
            "env -i",
            "scripts/clone_source.sh",
            "scripts/remove_signatures.sh",
            "scripts/verify_unsigned_ipa.sh",
            "--build-mode local_xcode",
        ):
            self.assertIn(required, text)
        self.assertNotIn("sudo", text)
        self.assertNotIn("CODE_SIGNING_ALLOWED=YES", text)

    def test_submodule_recorder_accepts_repository_without_submodules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            subprocess.run(["git", "init", "-q", root], check=True)
            tracked = root / "README.md"
            tracked.write_text("fixture\n", encoding="utf-8")
            subprocess.run(["git", "-C", root, "add", "README.md"], check=True)
            output = root / "submodules.json"
            subprocess.run([sys.executable, SUBMODULES, root, output], check=True)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), [])

    def test_android_local_builder_requires_exact_sdk_and_isolation(self) -> None:
        self.assertTrue(ANDROID_LOCAL.stat().st_mode & stat.S_IXUSR)
        text = ANDROID_LOCAL.read_text(encoding="utf-8")
        for required in (
            "scripts/validate_android_target.py", "Working tree must be clean", "env -i",
            "scripts/clone_source.sh", "scripts/build_android_apk.sh", "scripts/verify_android_apk.py",
            "--build-mode local_android_sdk", "platforms/android-$compile_sdk/android.jar",
        ):
            self.assertIn(required, text)
        self.assertNotIn("sdkmanager", text)
        self.assertNotIn("sudo", text)
        build_script = (ROOT / "scripts" / "build_android_apk.sh").read_text(encoding="utf-8")
        self.assertIn('-mindepth 3 -maxdepth 3 -type f -path \'*/bin/gradle\'', build_script)

    def test_firetv_installer_requires_digest_confirmation_and_refuses_replacement(self) -> None:
        self.assertTrue(FIRETV_INSTALL.stat().st_mode & stat.S_IXUSR)
        text = FIRETV_INSTALL.read_text(encoding="utf-8")
        for required in ("--confirm-install", "pm path", "Refusing to replace or uninstall", "AFTKRT", "keyevent"):
            self.assertIn(required, text)
        self.assertNotIn("adb uninstall", text)
        self.assertNotIn(' install -r ', text)


if __name__ == "__main__":
    unittest.main()
