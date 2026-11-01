from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from support import IsolatedTest
from drivesync import config, filters, i18n
from drivesync.state import execution_lock


class CliTests(IsolatedTest):
    def test_init_and_resync_real_runs_use_configured_transfer_options(self):
        self.config.update(transfers=2, init_transfers=10, checkers=12,
                           init_extra_flags=["--drive-pacer-min-sleep", "20ms"])
        config.save(self.paths, self.config)
        for operation in ("init", "resync"):
            with self.subTest(operation=operation):
                self.assertEqual(self.cli(operation, "docs", "--dry-run")[0], 0)
                dry = self.calls()[-1]["argv"]
                self.assertEqual(dry[dry.index("--transfers") + 1], "2")
                self.assertNotIn("--drive-pacer-min-sleep", dry)
                code, _, error = self.cli(operation, "docs", "--yes")
                self.assertEqual(code, 0, error)
                real = self.calls()[-1]["argv"]
                for option, value in (("--transfers", "10"), ("--checkers", "12"),
                                      ("--drive-pacer-min-sleep", "20ms")):
                    self.assertEqual(real[real.index(option) + 1], value)
                self.assertNotIn("--drive-pacer-burst", real)

    def test_initialization_requires_successful_dry_run(self):
        code, _, error = self.cli("init", "docs", "--yes")
        self.assertEqual(code, 1)
        self.assertIn(i18n.DRY_RUN_REQUIRED, error)
        self.assertEqual(self.calls(), [])
        self.assertFalse((Path(self.config["folders"][0]["local"]) / ".drivesync-check").exists())

    def test_dry_run_then_initialization(self):
        self.settings(strict_check_access=True)
        code, output, error = self.cli("init", "docs", "--dry-run")
        self.assertEqual(code, 0, error)
        self.assertIn("Drive'a 1, bilgisayara 1 kopya, 1 silme", output)
        self.assertFalse(config.load(self.paths)["folders"][0]["initialized"])
        self.assertEqual([c["argv"][0] for c in self.calls()], ["mkdir", "bisync"])
        self.assertNotIn("--check-access", self.calls()[1]["argv"])
        code, output, error = self.cli("init", "docs", "--yes")
        self.assertEqual(code, 0, error)
        calls = [c["argv"] for c in self.calls()]
        self.assertEqual([c[0] for c in calls], ["mkdir", "bisync", "mkdir", "copyto", "bisync"])
        self.assertIn("--check-access", calls[-1])
        self.assertIn("--resync", calls[-1])
        self.assertNotIn("--dry-run", calls[-1])
        self.assertTrue(config.load(self.paths)["folders"][0]["initialized"])
        self.assertEqual(self.store.read()["folders"]["docs"]["status"], "ok")

    def test_failed_dry_run_does_not_authorize_initialization(self):
        self.settings(dry_run={"code": 5, "log": "ERROR : simulated failure\n"})
        self.assertEqual(self.cli("init", "docs", "--dry-run")[0], 1)
        self.assertEqual(self.cli("init", "docs", "--yes")[0], 1)
        self.assertEqual(len(self.calls()), 2)
        self.assertIsNone(self.store.read()["folders"]["docs"]["dry_run_ok_at"])

    def test_expired_dry_run_is_rejected(self):
        self.assertEqual(self.cli("init", "docs", "--dry-run")[0], 0)
        self.store.folder("docs", dry_run_ok_at=(datetime.now(timezone.utc) - timedelta(hours=25)).isoformat())
        self.assertEqual(self.cli("init", "docs", "--yes")[0], 1)
        self.assertEqual(len(self.calls()), 2)

    def test_filter_or_target_change_invalidates_dry_run(self):
        for field in ("filters", "target"):
            with self.subTest(field=field):
                self.assertEqual(self.cli("init", "docs", "--dry-run")[0], 0)
                value = config.load(self.paths)
                if field == "filters":
                    value["excludes"].append("*.another")
                else:
                    value["folders"][0]["remote_name"] = "Changed"
                config.save(self.paths, value)
                self.assertEqual(self.cli("init", "docs", "--yes")[0], 1)

    def test_resync_requires_dry_run_and_clears_safety_state(self):
        self.prepare(initialized=True)
        self.store.folder("docs", status="error", needs_resync=True, last_error=i18n.RESYNC_REQUIRED)
        self.assertEqual(self.cli("resync", "docs", "--yes")[0], 1)
        self.assertEqual(self.cli("resync", "docs", "--dry-run")[0], 0)
        self.assertTrue(self.store.read()["folders"]["docs"]["needs_resync"])
        self.assertEqual(self.cli("resync", "docs", "--yes")[0], 0)
        self.assertFalse(self.store.read()["folders"]["docs"]["needs_resync"])

    def test_delete_block_requires_confirmation_and_force_is_once(self):
        self.prepare(initialized=True)
        self.store.folder("docs", status="blocked_delete")
        with patch("builtins.input", return_value="n"):
            self.assertEqual(self.cli("force-sync", "docs")[0], 1)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.cli("force-sync", "docs", "--yes")[0], 0)
        command = self.calls()[-1]["argv"]
        self.assertIn("--force", command)
        self.assertNotIn("--resync", command)
        self.assertNotIn("--force", config.load(self.paths))
        self.assertEqual(self.cli("force-sync", "docs", "--yes")[0], 1)

    def test_force_cannot_bypass_resync(self):
        self.prepare(initialized=True)
        self.store.folder("docs", status="blocked_delete", needs_resync=True)
        self.assertEqual(self.cli("force-sync", "docs", "--yes")[0], 1)
        self.assertEqual(self.calls(), [])

    def test_all_direct_operations_refuse_held_execution_lock(self):
        with execution_lock(self.paths, "docs"):
            for command in (("init", "docs", "--dry-run"), ("resync", "docs", "--yes"),
                            ("force-sync", "docs", "--yes")):
                code, _, error = self.cli(*command)
                self.assertEqual(code, 1)
                self.assertIn(i18n.BUSY, error)
        self.assertEqual(self.calls(), [])

    def test_failed_copyto_prevents_bisync(self):
        self.assertEqual(self.cli("init", "docs", "--dry-run")[0], 0)
        self.settings(copyto={"code": 5})
        self.assertEqual(self.cli("init", "docs", "--yes")[0], 1)
        self.assertEqual([c["argv"][0] for c in self.calls()], ["mkdir", "bisync", "mkdir", "copyto"])
        self.assertFalse(config.load(self.paths)["folders"][0]["initialized"])

    def test_status_and_logs_without_daemon(self):
        code, output, _ = self.cli("status")
        self.assertEqual(code, 0)
        self.assertIn(i18n.NO_DAEMON, output)
        self.assertIn("docs", output)
        self.assertEqual(self.cli("logs", "docs")[1].strip(), i18n.NO_LOG)
        self.store.folder("docs", last_log=str(self.paths.logs / "a.log"))
        self.assertEqual(self.cli("logs", "docs")[1].strip(), str(self.paths.logs / "a.log"))
        self.assertEqual(self.cli("logs", "unknown")[0], 1)

    def test_sync_pause_resume_require_daemon(self):
        for command in ("sync", "pause", "resume"):
            self.assertEqual(self.cli(command)[0], 1)
        self.assertEqual(self.calls(), [])

    def test_config_path_has_no_side_effects(self):
        self.paths.config.unlink()
        code, output, _ = self.cli("config-path")
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), str(self.paths.config))
        self.assertFalse(self.paths.config.exists())

    def test_failed_dry_runs_report_errors_without_success_and_recover(self):
        for exit_code in (5, 7):
            with self.subTest(exit_code=exit_code):
                self.settings(dry_run={"code": exit_code, "log":
                    "2026/11/01 12:00:00 ERROR : directory not found\n"
                    "ERROR : Bisync aborted. Must run --resync to recover.\n"})
                code, output, error = self.cli("init", "docs", "--dry-run")
                self.assertNotEqual(code, 0)
                self.assertIn(i18n.DRY_FAILED, error)
                self.assertIn("directory not found", error)
                self.assertIn("Bisync aborted", error)
                self.assertNotIn("Deneme: Drive'a", output)
                self.assertNotIn(i18n.DRY_OK, output)
                self.assertNotIn(i18n.DRY_ACTIONS, output)
                item = self.store.read()["folders"]["docs"]
                self.assertEqual(item["status"], "not_initialized")
                self.assertFalse(item["needs_resync"])
                self.assertIsNone(item["dry_run_ok_at"])
                self.assertIn("directory not found", item["last_error"])
                self.settings()
                self.assertEqual(self.cli("init", "docs", "--dry-run")[0], 0)
                item = self.store.read()["folders"]["docs"]
                self.assertIsNone(item["last_error"])
                self.assertFalse(item["needs_resync"])
                self.assertIsNotNone(item["dry_run_ok_at"])

    def test_mkdir_failure_stops_dry_run_before_bisync(self):
        self.settings(mkdir={"code": 5, "output": "ERROR : cannot create directory"})
        code, output, error = self.cli("init", "docs", "--dry-run")
        self.assertEqual(code, 1)
        self.assertIn(i18n.DRY_FAILED, error)
        self.assertIn("cannot create directory", error)
        self.assertNotIn(i18n.REMOTE_READY, output)
        self.assertNotIn("Deneme: Drive'a", output)
        self.assertEqual([c["argv"][0] for c in self.calls()], ["mkdir"])
        item = self.store.read()["folders"]["docs"]
        self.assertEqual(item["status"], "not_initialized")
        self.assertFalse(item["needs_resync"])
        self.assertIsNone(item["dry_run_ok_at"])

    def test_status_errors_are_short_single_lines_without_timestamp(self):
        self.store.folder("docs", last_error="2026/11/01 12:00:00 INFO : diagnostic\n"
                          "2026/11/01 12:00:00 ERROR : " + "x" * 120 +
                          "\nERROR : second message")
        output = self.cli("status")[1]
        error_line = output.splitlines()[-1]
        self.assertEqual(len(error_line.strip()), 80)
        self.assertTrue(error_line.endswith("…"))
        self.assertNotIn("2026/11/01", output)
        self.assertNotIn("second message", output)
        self.assertNotIn("ERROR", output)

    def test_successful_cli_does_not_initialize_libnotify(self):
        with patch("gi.require_version", side_effect=AssertionError("unexpected GI init")):
            self.assertEqual(self.cli("init", "docs", "--dry-run")[0], 0)
            self.assertEqual(self.cli("init", "docs", "--yes")[0], 0)

    def test_failed_initialized_resync_dry_run_keeps_safety_requirement(self):
        self.prepare(initialized=True)
        self.settings(dry_run={"code": 7, "log": "ERROR : initialized pair failed\n"})
        # Exercise the reporting path without sending a desktop notification.
        with patch("drivesync.cli.Notifier"):
            code, output, error = self.cli("resync", "docs", "--dry-run")
        self.assertEqual(code, 1)
        self.assertIn(i18n.DRY_FAILED, error)
        self.assertIn("initialized pair failed", error)
        self.assertNotIn("Deneme: Drive'a", output)
        item = self.store.read()["folders"]["docs"]
        self.assertEqual(item["status"], "error")
        self.assertTrue(item["needs_resync"])
        self.assertIsNone(item["dry_run_ok_at"])
