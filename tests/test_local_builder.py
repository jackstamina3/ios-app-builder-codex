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


if __name__ == "__main__":
    unittest.main()
