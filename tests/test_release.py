"""Public-release setup, preview and installer coverage with no network access."""

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch
import zipfile

from support import IsolatedTest, ROOT
from drivesync import config, i18n, init_flow, install_rclone, prerequisites
from drivesync.daemon import Daemon
from drivesync.presentation import present


class ReleaseTests(IsolatedTest):
    def test_default_binary_path_precedence_and_existing_config_untouched(self):
        with patch("drivesync.config.shutil.which", return_value="/usr/bin/rclone"):
            self.assertEqual(config.defaults()["rclone_path"], "/usr/bin/rclone")
        with patch("drivesync.config.shutil.which", return_value=None):
            self.assertEqual(config.defaults()["rclone_path"], str(Path.home() / ".local/bin/rclone"))
        before = self.paths.config.read_bytes()
        config.load(self.paths)
        self.assertEqual(self.paths.config.read_bytes(), before)
        self.assertEqual(config.load(self.paths)["folders"], self.config["folders"])

    def test_versions_boundary_missing_invalid_and_timeout(self):
        for version, valid in (("rclone v1.65.9", False), ("rclone v1.66.0", True),
                               ("rclone v1.100.2", True), ("rclone v2.0.0", True),
                               ("garbage", False)):
            self.settings(version=dict(output=version))
            if valid:
                prerequisites.check_version(self.config["rclone_path"])
            else:
                with self.assertRaisesRegex(prerequisites.SetupError, "1.66"):
                    prerequisites.check_version(self.config["rclone_path"])
        for failure in (FileNotFoundError(), subprocess.TimeoutExpired("rclone", 10)):
            with patch("drivesync.prerequisites.subprocess.run", side_effect=failure):
                with self.assertRaises(prerequisites.SetupError):
                    prerequisites.check_version("missing")

    def test_missing_remote_matches_whole_name_and_cli_reports_fix(self):
        self.settings(listremotes=dict(output="testing:\nother:"))
        code, out, error = self.cli("init", "docs", "--dry-run", "--json")
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)["ok"])
        self.assertIn("Google Drive bağlantısı kurulmamış", json.loads(out)["error"]["message"])
        self.assertIn(i18n.INSTALL_LINK, json.loads(out)["error"]["message"])
        self.assertFalse(self.calls())
        self.assertIn(i18n.REMOTE_MISSING, self.cli("status")[1])
        self.settings(listremotes=dict(code=1, output="client_secret=hidden"))
        with self.assertRaisesRegex(prerequisites.SetupError, "listremotes"):
            prerequisites.check(self.config)

    def test_daemon_and_frontends_surface_startup_errors_and_recover(self):
        for settings, message in ((dict(version=dict(output="rclone v1.65.0")), i18n.RCLONE_REQUIRED),
                                  (dict(listremotes=dict(output="")), i18n.REMOTE_MISSING)):
            self.settings(**settings)
            daemon = Daemon(self.paths)
            try:
                self.assertFalse(daemon.reload_config())
                status = json.loads(daemon.status_json())
                self.assertEqual(status["daemon"]["status"], "error")
                self.assertEqual(status["daemon"]["last_error"], message)
                self.assertEqual(present(status).text, message)
                self.assertEqual(present(dict(status, paused=True)).text, message)
                from drivesync.app_model import problem_summary
                self.assertIn(message, problem_summary(status))
                self.assertFalse(daemon.sync_now())
                self.settings()
                self.assertTrue(daemon.reload_config())
                self.assertIsNone(daemon.store.read()["daemon"]["last_error"])
            finally:
                daemon.watchers.close()

    def test_json_preview_is_single_document_bounded_and_failure_is_not_success(self):
        self.settings(dry_run=dict(log="".join(
            f"INFO : - Path1 Queue copy to Path2 - file-{i}.txt\n" for i in range(80))))
        code, output, errors = self.cli("init", "docs", "--dry-run", "--json")
        self.assertEqual(code, 0, errors)
        plan = init_flow.parse_plan(output)
        self.assertEqual(plan.to_remote, 80)
        self.assertEqual(len(plan.actions), 50)
        self.assertTrue(plan.can_start("Documents"))
        self.settings(dry_run=dict(code=7, log="ERROR : failure\n"))
        code, output, error = self.cli("init", "docs", "--dry-run", "--json")
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(output)["ok"])
        self.assertIn(i18n.DRY_FAILED, error)
        self.assertIsNone(self.store.read()["folders"]["docs"]["dry_run_ok_at"])
        self.assertEqual(self.cli("init", "docs", "--json")[0], 1)

    def test_deletion_confirmation_unknowns_and_invalid_plans(self):
        plan = init_flow.Plan(1, 2, 3, 0, ())
        for name in ("", "documents", " Documents", "Documents "):
            self.assertFalse(plan.can_start("Documents", name))
        self.assertTrue(plan.can_start("Documents", "Documents"))
        self.assertFalse(init_flow.Plan(1, 2, 0, 1, ()).can_start("Documents"))
        good = dict(to_remote=1, to_local=2, deletes=0, unknown=0, actions=[])
        for change in (dict(deletes=True), dict(to_local=-1), dict(unknown="0"),
                       dict(actions=[1]), dict(actions=["x"] * 51)):
            with self.assertRaises(ValueError):
                init_flow.parse_plan(json.dumps(dict(good, **change)))
        for bad in ("", "[]", "{}", "null"):
            with self.assertRaises(ValueError):
                init_flow.parse_plan(bad)

    def test_detached_command_preserves_paths_without_shell_interpretation(self):
        command = init_flow.submit_command("/a path/drivesync-cli", "docs")
        self.assertEqual(command, ["/a path/drivesync-cli", "init", "docs", "--yes", "--json"])
        self.assertNotIn("systemd-run", command)
        with self.assertRaises(ValueError):
            init_flow.submit_command("cli", "../bad")
        folder = self.config["folders"][0]
        self.assertEqual(init_flow.init_label(folder, {}), i18n.INIT_START)
        self.assertEqual(init_flow.init_label(dict(folder, initialized=True), dict(needs_resync=True)), i18n.INIT_RESYNC)
        for state in ("syncing", "queued"):
            self.assertIsNone(init_flow.init_label(folder, dict(status=state)))

    def test_version_and_cli_add_folder(self):
        result = subprocess.run([str(ROOT / "bin/drivesync-cli"), "--version"],
                                env=os.environ, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "0.2.0")
        directory = self.root / "New Folder"
        directory.mkdir()
        code, output, error = self.cli("add-folder", str(directory), "--name", "Yeni")
        self.assertEqual(code, 0, error)
        self.assertIn("yeni", output)
        self.assertFalse(config.load(self.paths)["folders"][-1]["initialized"])
        self.assertEqual(self.cli("add-folder", str(directory), "--name", "Yeni")[0], 1)


class OAuthTests(IsolatedTest):
    def setUp(self):
        super().setUp()
        self.client = self.root / "client.json"
        self.client.write_text(json.dumps(dict(installed=dict(
            client_id="example.apps.googleusercontent.com", client_secret="private-value"))))
        self.conf = self.root / "rclone.conf"
        self.conf.write_text("private-existing-token")

    def fake_run(self, argv, **kwargs):
        command = argv[1:]
        output = "private-value private-existing-token"
        if command == ["version"]:
            output = "rclone v1.66.0"
        elif command == ["listremotes"]:
            output = "test:" if self.existing else ""
        elif command == ["config", "file"]:
            output = "Configuration file is stored at:\n" + str(self.conf)
        return subprocess.CompletedProcess(argv, self.failure if command[0] == "about" else 0, output, "private-value")

    def run_setup(self, *, existing=False, replace=False, failure=0):
        self.existing, self.failure = existing, failure
        with patch("drivesync.setup_remote.subprocess.run", side_effect=self.fake_run) as run:
            result = self.cli("setup-remote", "--client-json", str(self.client), "--remote", "test",
                              *(["--replace", "--yes"] if replace else []))
        for output in result[1:]:
            self.assertNotIn("private-value", output)
            self.assertNotIn("private-existing-token", output)
        return result, run

    def test_setup_permissions_browser_command_and_about_verification(self):
        self.config["remote"] = "previous:"
        config.save(self.paths, self.config)
        (code, output, error), run = self.run_setup()
        self.assertEqual(code, 0, error)
        self.assertIn("bağlantısı kuruldu", output)
        self.assertEqual(config.load(self.paths)["remote"], "test:")
        stored = self.paths.config_dir / "google-client.json"
        self.assertEqual(stored.read_bytes(), self.client.read_bytes())
        self.assertEqual(stored.stat().st_mode & 0o777, 0o600)
        commands = [call.args[0][1:] for call in run.call_args_list]
        self.assertIn(["config", "create", "test", "drive", "client_id=example.apps.googleusercontent.com",
                       "client_secret=private-value", "scope=drive"], commands)
        self.assertEqual(commands[-1], ["about", "test:"])

    def test_existing_remote_refuses_without_writes_and_replace_backs_up_first(self):
        (code, _, error), run = self.run_setup(existing=True)
        self.assertEqual(code, 1)
        self.assertIn("--replace", error)
        self.assertFalse((self.paths.config_dir / "google-client.json").exists())
        self.assertFalse(list(self.paths.config_dir.glob("rclone.conf.backup-*")))
        (code, _, error), run = self.run_setup(existing=True, replace=True)
        self.assertEqual(code, 0, error)
        backup, = self.paths.config_dir.glob("rclone.conf.backup-*")
        self.assertEqual(backup.read_bytes(), self.conf.read_bytes())
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        commands = [call.args[0][1:3] for call in run.call_args_list]
        self.assertLess(commands.index(["config", "file"]), commands.index(["config", "create"]))
        creation = next(c.args[0] for c in run.call_args_list if c.args[0][1:3] == ["config", "create"])
        self.assertIn("token=", creation)

    def test_backup_failure_prevents_replace_and_about_failure_has_safe_error(self):
        self.conf.unlink()
        (code, _, error), run = self.run_setup(existing=True, replace=True)
        self.assertEqual(code, 1)
        self.assertIn(i18n.REMOTE_BACKUP_FAILED, error)
        self.assertFalse(any(c.args[0][1:3] == ["config", "create"] for c in run.call_args_list))
        (code, _, error), _ = self.run_setup(failure=1)
        self.assertEqual(code, 1)
        self.assertIn(i18n.REMOTE_SETUP_FAILED, error)

    def test_invalid_json_web_client_and_bad_remote_never_run_rclone(self):
        for value in ("bad JSON", "{}", '{"web": {}}', '{"installed": null}',
                      '{"installed":{"client_id":1,"client_secret":"x"}}'):
            self.client.write_text(value)
            with patch("drivesync.setup_remote.subprocess.run") as run:
                self.assertEqual(self.cli("setup-remote", "--client-json", str(self.client))[0], 1)
                run.assert_not_called()
        with patch("drivesync.setup_remote.subprocess.run") as run:
            self.assertEqual(self.cli("setup-remote", "--client-json", str(self.client), "--remote", "bad:")[0], 1)
            run.assert_not_called()


class RcloneInstallerTests(IsolatedTest):
    def payloads(self, architecture):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as bundle:
            bundle.writestr(f"rclone-v1.70.0-linux-{architecture}/rclone", b"fake binary")
            bundle.writestr("../../escape", "must never extract")
        archive = f"rclone-v1.70.0-linux-{architecture}.zip"
        return {install_rclone.BASE + "/version.txt": b"rclone v1.70.0\n",
                install_rclone.BASE + "/v1.70.0/SHA256SUMS":
                    (hashlib.sha256(output.getvalue()).hexdigest() + "  " + archive + "\n").encode(),
                install_rclone.BASE + "/v1.70.0/" + archive: output.getvalue()}

    def test_verified_local_install_both_architectures_no_archive_extraction(self):
        for machine, architecture in (("x86_64", "amd64"), ("aarch64", "arm64")):
            data = self.payloads(architecture)
            target = self.root / machine / "rclone"
            with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
                result = install_rclone.install(fetch=data.__getitem__, machine=machine, destination=target)
            self.assertEqual(result, target)
            self.assertEqual(target.read_bytes(), b"fake binary")
            self.assertEqual(target.stat().st_mode & 0o777, 0o755)
            self.assertFalse((self.root / "escape").exists())

    def test_bad_checksum_missing_checksum_and_download_error_preserve_existing(self):
        target = self.root / "rclone"
        target.write_bytes(b"keep")
        for checksums in (b"", ("0" * 64 + "  rclone-v1.70.0-linux-amd64.zip\n").encode()):
            data = self.payloads("amd64")
            data[install_rclone.BASE + "/v1.70.0/SHA256SUMS"] = checksums
            with self.assertRaises(ValueError):
                install_rclone.install(fetch=data.__getitem__, machine="x86_64", destination=target)
            self.assertEqual(target.read_bytes(), b"keep")
        with self.assertRaises(OSError):
            install_rclone.install(fetch=lambda _: (_ for _ in ()).throw(OSError()), machine="x86_64", destination=target)
        self.assertEqual(target.read_bytes(), b"keep")
        with self.assertRaises(ValueError):
            install_rclone.install(machine="mips", destination=target)
