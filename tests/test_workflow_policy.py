import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
ACTION = re.compile(r"^\s*uses:\s*([^@\s]+)@([^\s#]+)", re.MULTILINE)


class WorkflowPolicyTests(unittest.TestCase):
    def workflow_texts(self):
        paths = sorted(WORKFLOWS.glob("*.yml"))
        self.assertEqual(
            [path.name for path in paths],
            [
                "build-android-apk.yml",
                "build-unsigned-ipa.yml",
                "probe-android-source.yml",
                "probe-source.yml",
            ],
        )
        return {path.name: path.read_text(encoding="utf-8") for path in paths}

    def test_manual_dispatch_is_the_only_trigger(self):
        forbidden = re.compile(
            r"^\s{2}(?:push|pull_request|pull_request_target|issue_comment|schedule|workflow_call):",
            re.MULTILINE,
        )
        for name, text in self.workflow_texts().items():
            with self.subTest(workflow=name):
                self.assertRegex(text, r"(?m)^on:\n  workflow_dispatch:")
                self.assertNotRegex(text, forbidden)

    def test_workflow_permissions_are_read_only(self):
        write_permission = re.compile(r"^\s+[a-z-]+:\s*write\s*$", re.MULTILINE)
        for name, text in self.workflow_texts().items():
            with self.subTest(workflow=name):
                self.assertRegex(text, r"(?m)^permissions:\n  contents: read\s*$")
                self.assertNotRegex(text, write_permission)

    def test_only_github_owned_actions_are_full_sha_pinned(self):
        for name, text in self.workflow_texts().items():
            actions = ACTION.findall(text)
            self.assertTrue(actions, name)
            for action, revision in actions:
                with self.subTest(workflow=name, action=action):
                    self.assertTrue(action.startswith("actions/"))
                    self.assertRegex(revision, r"^[0-9a-f]{40}$")

    def test_every_checkout_disables_persisted_credentials(self):
        for name, text in self.workflow_texts().items():
            checkout_count = len(re.findall(r"uses:\s*actions/checkout@", text))
            disabled_count = len(re.findall(r"persist-credentials:\s*false", text))
            with self.subTest(workflow=name):
                self.assertEqual(checkout_count, disabled_count)

    def test_build_uses_plan_build_and_fresh_verify_jobs(self):
        text = self.workflow_texts()["build-unsigned-ipa.yml"]
        self.assertRegex(text, r"(?m)^  plan:\n")
        self.assertRegex(text, r"(?m)^  build:\n")
        self.assertRegex(text, r"(?m)^  verify:\n")
        self.assertIn("runs-on: ${{ needs.plan.outputs.runner }}", text)
        self.assertIn("Target requires Xcode build", text)
        self.assertIn("name: quarantine-${{ inputs.request_id }}", text)
        self.assertIn("name: unsigned-ipa-${{ inputs.request_id }}", text)

    def test_android_build_has_quarantine_fresh_verify_and_ephemeral_signing(self):
        text = self.workflow_texts()["build-android-apk.yml"]
        self.assertRegex(text, r"(?m)^  plan:\n")
        self.assertRegex(text, r"(?m)^  build:\n")
        self.assertRegex(text, r"(?m)^  verify:\n")
        self.assertIn("runs-on: ${{ needs.plan.outputs.runner }}", text)
        self.assertIn("name: android-quarantine-${{ inputs.request_id }}", text)
        self.assertIn("name: test-signed-apk-${{ inputs.request_id }}", text)
        self.assertIn("scripts/build_android_apk.sh", text)
        self.assertIn("Perform independent full Fire TV verification", text)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("sdkmanager", text)
        self.assertNotIn("debug.keystore", text)


if __name__ == "__main__":
    unittest.main()
