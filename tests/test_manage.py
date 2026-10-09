import copy
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import tomllib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("router_manage", ROOT / "manage.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class FakeService:
    def __init__(self):
        self.running = False
        self.fail_start = 0
        self.fail_stop = False
        self.busy = False

    def start(self, plist):
        if self.fail_start:
            self.fail_start -= 1
            raise RuntimeError("fixture startup failure")
        self.running = True

    def stop(self):
        if self.fail_stop:
            raise RuntimeError("fixture stop failure")
        self.running = False

    def health(self, port):
        if not self.running:
            raise RuntimeError("unhealthy")

    def idle(self, port, secret):
        if self.busy:
            raise RuntimeError("busy")


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "codex"
        self.home.mkdir()
        self.original = 'model = "gpt-fixture"\nmodel_reasoning_effort = "high"\n[features]\nx = true\n'
        (self.home / "config.toml").write_text(self.original)
        self.gpt = {"slug": "gpt-fixture", "supports_search_tool": True, "context_window": 258400,
                    "base_instructions": "This fixture must remain exactly unchanged"}
        (self.home / "models_cache.json").write_text(json.dumps({"models": [self.gpt]}))
        self.settings = self.root / "settings.json"
        self.data = json.loads((ROOT / "config/router.example.json").read_text())
        for row in self.data["routes"].values():
            row["base_url"] = "http://127.0.0.1:18999/v1"
        self.settings.write_text(json.dumps(self.data))
        self.service = FakeService()
        self.manager = m.Manager(self.home, self.root / "LaunchAgents", self.service)
        mac = patch.object(m.platform, "system", return_value="Darwin")
        mac.start()
        self.addCleanup(mac.stop)
        self.auth = self.home / "auth.json"
        self.auth.write_text("unread fixture credentials")

    def install(self):
        # Port 0 is intentionally not used: preflight verifies the supplied port.
        import socket
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        return self.manager.install(self.settings, port=port)

    def test_install_restore_preserves_later_unrelated_settings(self):
        self.install()
        installed = tomllib.loads(self.manager.config.read_text())
        self.assertEqual(installed["model_reasoning_effort"], "high")
        catalog = json.loads((self.manager.target / "models.json").read_text())
        self.assertEqual(catalog["models"][0], self.gpt)
        self.assertEqual(len(catalog["models"]), 3)
        self.assertEqual([x["context_window"] for x in catalog["models"][1:]], [1048576, 1048576])
        with self.manager.config.open("a") as stream:
            stream.write('\n[projects."/example/project"]\ntrust_level = "trusted"\n')
        result = self.manager.restore()
        self.assertTrue(result["restored"])
        restored = tomllib.loads(self.manager.config.read_text())
        expected = tomllib.loads(self.original)
        expected["projects"] = {"/example/project": {"trust_level": "trusted"}}
        self.assertEqual(restored, expected)
        self.assertFalse(self.service.running)
        self.assertFalse(self.manager.plist.exists())
        self.assertEqual(self.auth.read_text(), "unread fixture credentials")

    def test_start_failure_rolls_back(self):
        self.service.fail_start = 1
        with self.assertRaises(RuntimeError):
            self.install()
        self.assertEqual(self.manager.config.read_text(), self.original)
        self.assertFalse(self.manager.target.exists())
        self.assertFalse(self.manager.plist.exists())

    def test_concurrent_config_edit_is_preserved(self):
        def modified(port):
            self.manager.config.write_text(self.original + "\n# concurrent edit\n")
        self.service.health = modified
        with self.assertRaises(RuntimeError):
            self.install()
        self.assertIn("concurrent edit", self.manager.config.read_text())
        self.assertFalse(self.manager.target.exists())

    def test_existing_provider_is_preserved(self):
        self.manager.config.write_text('model_provider = "another_provider"\n')
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse(self.manager.target.exists())

    def test_restore_refuses_changed_owned_field(self):
        self.install()
        raw = self.manager.config.read_text().replace('model = "gpt-fixture"', 'model = "glm-5.3-flash"')
        self.manager.config.write_text(raw)
        with self.assertRaises(ValueError):
            self.manager.restore()
        self.assertEqual(self.manager.config.read_text(), raw)
        self.assertTrue(self.service.running)

    def test_restore_stop_failure_rolls_config_back(self):
        self.install()
        raw = self.manager.config.read_bytes()
        self.service.fail_stop = True
        with self.assertRaises(RuntimeError):
            self.manager.restore()
        self.assertEqual(self.manager.config.read_bytes(), raw)
        self.assertTrue(self.manager.plist.exists())

    def test_backup_tamper_rejected(self):
        self.install()
        Path(self.manager.state()["backup"]).write_text("changed")
        with self.assertRaises(ValueError):
            self.manager.plan_restore()

    def test_toggle_changes_only_company_flags(self):
        self.install()
        before = json.loads((self.manager.target / "models.json").read_text())
        self.manager.tool_search(False)
        after = json.loads((self.manager.target / "models.json").read_text())
        expected = copy.deepcopy(before)
        for model in expected["models"][1:]:
            model["supports_search_tool"] = False
        self.assertEqual(after, expected)
        self.manager.tool_search(True)
        self.assertEqual(json.loads((self.manager.target / "models.json").read_text()), before)

    def test_toggle_failed_restart_restores_files(self):
        self.install()
        before = [(self.manager.target / name).read_bytes() for name in ("router.local.json", "models.json")]
        self.service.fail_start = 1
        with self.assertRaises(RuntimeError):
            self.manager.tool_search(False)
        self.assertEqual(before, [(self.manager.target / name).read_bytes() for name in ("router.local.json", "models.json")])
        self.assertTrue(self.service.running)

    def test_busy_restore_is_rejected(self):
        self.install()
        self.service.busy = True
        with self.assertRaises(RuntimeError):
            self.manager.restore()
        self.assertTrue(self.service.running)

    def test_wrapped_service_is_preserved(self):
        self.install()
        plist = plistlib.loads(self.manager.plist.read_bytes())
        plist["ProgramArguments"][1] = "/example/native_bridge.py"
        self.manager.plist.write_bytes(plistlib.dumps(plist))
        with self.assertRaises(ValueError):
            self.manager.restore()
        self.assertTrue(self.service.running)

    def test_skill_is_optional_and_existing_copy_protected(self):
        self.install()
        self.assertFalse((self.home / "skills").exists())
        self.manager.install_skill()
        self.assertTrue((self.home / "skills/multi-model-workflow/SKILL.md").exists())
        self.assertFalse((self.home / "AGENTS.md").exists())
        with self.assertRaises(ValueError):
            self.manager.install_skill()

    def test_installed_secrets_and_backup_are_private(self):
        self.install()
        for path in [self.manager.plist, self.manager.config, self.manager.target / "router.secret", Path(self.manager.state()["backup"])]:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_profile_using_router_blocks_restore(self):
        self.install()
        with self.manager.config.open("a") as stream:
            stream.write('\n[profiles.custom]\nmodel_provider = "multi_model_router"\n')
        with self.assertRaises(ValueError):
            self.manager.restore()


if __name__ == "__main__":
    unittest.main()
