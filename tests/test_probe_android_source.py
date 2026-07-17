from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "scripts" / "probe_android_source.py"


class AndroidProbeTests(unittest.TestCase):
    def test_probe_is_static_and_distinguishes_root_plugin_declaration(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            (source / "app" / "src" / "main").mkdir(parents=True)
            (source / "gradle" / "wrapper").mkdir(parents=True)
            (source / "gradlew").write_text("#!/bin/sh\nexit 99\n")
            (source / "gradle" / "wrapper" / "gradle-wrapper.properties").write_text(
                "distributionUrl=https\\://services.gradle.org/distributions/gradle-8.13-bin.zip\n"
            )
            (source / "build.gradle.kts").write_text("alias(libs.plugins.android.application) apply false\n")
            (source / "app" / "build.gradle.kts").write_text('''
plugins { alias(libs.plugins.android.application) }
android {
  compileSdk = 36
  defaultConfig { applicationId = "com.example.tv"; minSdk = 24; targetSdk = 36; versionCode = 1; versionName = "1.0" }
  productFlavors { create("full") {}; create("store") {} }
  signingConfigs { create("release") {} }
}
''')
            (source / "app" / "src" / "main" / "AndroidManifest.xml").write_text('''
<manifest><uses-feature android:name="android.hardware.touchscreen" android:required="false"/>
<application android:banner="@mipmap/banner"><category android:name="android.intent.category.LEANBACK_LAUNCHER"/></application></manifest>
''')
            (source / "LICENSE").write_text("fixture")
            output = Path(directory) / "report.json"
            subprocess.run([
                sys.executable, PROBE, source, output, "--repository", "example/tv", "--ref", "1.0",
                "--commit", "a" * 40,
            ], check=True, capture_output=True, text=True)
            report = json.loads(output.read_text())
            self.assertEqual(len(report["application_modules"]), 1)
            self.assertEqual(report["application_modules"][0]["flavors"], ["full", "store"])
            self.assertTrue(report["requires_human_selection"])
            self.assertEqual(report["inspection"], "static_only_source_build_scripts_not_executed")


if __name__ == "__main__":
    unittest.main()
