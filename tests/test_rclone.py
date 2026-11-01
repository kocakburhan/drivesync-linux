from datetime import datetime, timedelta, timezone
from pathlib import Path

from support import IsolatedTest
from drivesync import config, i18n, rclone
from drivesync.queue import SyncQueue


class RcloneTests(IsolatedTest):
    def test_transfer_flags_by_run_type_and_config_overrides(self):
        for overrides in ({}, dict(transfers=3, init_transfers=12, checkers=16,
                                   init_extra_flags=["--drive-pacer-burst", "150"])):
            settings = dict(self.config, **overrides)
            for resync, dry_run, force in ((False, False, False), (True, False, False),
                                           (True, True, False), (False, False, True)):
                with self.subTest(overrides=overrides, resync=resync, dry_run=dry_run, force=force):
                    command = rclone.bisync_command(self.paths, settings, settings["folders"][0],
                                                   self.paths.logs / "run.log", resync=resync,
                                                   dry_run=dry_run, force=force)
                    self.assertEqual(command[command.index("--stats") + 1], "2s")
                    self.assertIn("--stats-one-line", command)
                    initial = resync and not dry_run
                    self.assertEqual(command[command.index("--transfers") + 1],
                                     str(settings["init_transfers"] if initial else settings["transfers"]))
                    self.assertEqual(command[command.index("--checkers") + 1], str(settings["checkers"]))
                    if initial:
                        start = command.index(settings["init_extra_flags"][0])
                        self.assertEqual(command[start:start + len(settings["init_extra_flags"])],
                                         settings["init_extra_flags"])
                    else:
                        self.assertNotIn("--drive-pacer-min-sleep", command)
                        self.assertNotIn("--drive-pacer-burst", command)

    def test_exact_safety_arguments_and_paths_with_spaces(self):
        folder = self.config["folders"][0]
        folder["local"] = str(self.root / "folder with spaces")
        command = rclone.bisync_command(self.paths, self.config, folder, self.paths.logs / "run.log")
        self.assertEqual(command[2:4], [folder["local"], "test:Linux-PC/Documents"])
        for option in ("--resilient", "--recover", "--check-access", "--skip-links",
                       "--drive-skip-gdocs", "--create-empty-src-dirs", "--filters-file"):
            self.assertIn(option, command)
        for option, value in (("--max-delete", "25"), ("--max-lock", "5m"),
                              ("--conflict-resolve", "newer"), ("--conflict-loser", "delete"),
                              ("--compare", "size,modtime"), ("--check-filename", ".drivesync-check")):
            self.assertEqual(command[command.index(option) + 1], value)
        self.assertNotIn("--resync", command)
        self.assertNotIn("--force", command)

    def test_resync_dryrun_and_force_are_explicit(self):
        command = rclone.bisync_command(self.paths, self.config, self.config["folders"][0],
                                        self.paths.logs / "test.log", resync=True, dry_run=True)
        self.assertIn("--resync", command)
        self.assertIn("--dry-run", command)
        self.assertEqual(command[command.index("--resync-mode") + 1], "newer")

    def test_fake_process_and_live_log_progress(self):
        log = rclone.new_log(self.paths, "docs")
        self.settings(bisync={"sleep": 0.4, "end_log": "INFO : final stats\n"})
        progress = []
        code, _ = rclone.run_sync(rclone.bisync_command(
            self.paths, self.config, self.config["folders"][0], log), log, progress.append)
        self.assertEqual(code, 0)
        self.assertTrue(any("Transferred:" in line for line in progress))
        self.assertIn("INFO : final stats", progress)
        self.assertEqual(len(self.calls()), 1)

    def test_missing_executable_is_a_failure(self):
        log = rclone.new_log(self.paths, "docs")
        code, _ = rclone.run_sync([str(self.root / "missing")], log)
        self.assertNotEqual(code, 0)

    def test_log_errors_conflicts_safety_and_limits(self):
        log = rclone.new_log(self.paths, "docs")
        log.write_text("ERROR " + "x" * 600 + "\nERROR two\nERROR three\nERROR four\n"
                       "NOTICE: - WARNING New or changed in both paths - a/report.txt\n"
                       "NOTICE: - WARNING New or changed in both paths - a/report.txt\n"
                       "NOTICE: - WARNING New or changed in both paths - b/report.txt\n"
                       "INFO : a/report.txt: The winner is: Path2\n"
                       "Safety abort: too many deletes\n")
        result = rclone.parse_log(log, 7)
        self.assertEqual(len(result.errors), 3)
        self.assertEqual(len(result.last_error), 500)
        self.assertEqual(result.conflicts, 2)
        self.assertTrue(result.blocked_delete)

    def test_filenames_and_general_conflict_diagnostics_are_not_events(self):
        log = rclone.new_log(self.paths, "docs")
        log.write_text("2026/11/01 01:34:14 INFO  : conflict.txt: Copied (new)\n"
                       "INFO : ERROR.txt: Copied (new)\n"
                       "INFO : Safety abort: too many deletes.txt: Copied (new)\n"
                       "INFO : Resolved 2 conflicts\n")
        result = rclone.parse_log(log, 0)
        self.assertEqual(result.conflicts, 0)
        self.assertFalse(result.blocked_delete)
        self.assertEqual(result.errors, [])

    def test_distinct_conflict_paths_preserve_spaces_and_separators(self):
        log = rclone.new_log(self.paths, "docs")
        log.write_text("NOTICE: - WARNING New or changed in both paths - a - b: c.txt\n"
                       "NOTICE: - WARNING New or changed in both paths - a - b: c.txt \n"
                       "NOTICE: - WARNING New or changed in both paths - a - b: c.txt\n")
        self.assertEqual(rclone.parse_log(log, 0).conflicts, 2)

    def test_dry_run_queues_and_skips_are_not_counted_twice(self):
        log = rclone.new_log(self.paths, "docs")
        log.write_text("INFO : - Path1 Queue copy to Path2 - new.txt\n"
                       "INFO : - Path2 Queue delete - old.txt\n"
                       "INFO : - Path1 Do queued copies to - Path2\n"
                       "NOTICE: new.txt: Skipped copy as --dry-run is set (size 5)\n"
                       "NOTICE: old.txt: Skipped delete as --dry-run is set\n")
        result = rclone.parse_log(log, 0)
        self.assertEqual((result.to_remote, result.to_local, result.deletes, result.unknown), (1, 0, 1, 0))

    def test_dryrun_summary_is_bounded(self):
        log = rclone.new_log(self.paths, "docs")
        log.write_text("Queue copy to Path2 - local\n" * 60 + "Queue copy to Path1 - remote\n"
                       "Queue delete - old\n")
        result = rclone.parse_log(log, 0)
        self.assertEqual((result.to_remote, result.to_local, result.deletes), (60, 1, 1))
        self.assertEqual(len(result.actions), 50)

    def test_resync_dryrun_notices_use_current_direction(self):
        log = rclone.new_log(self.paths, "docs")
        log.write_text("INFO : - Path2 Resync is copying files to - Path1\n"
                       "NOTICE : remote.txt: Skipped copy as --dry-run is set\n"
                       "INFO : - Path1 Resync is copying files to - Path2\n"
                       "NOTICE : local.txt: Skipped copy as --dry-run is set\n")
        result = rclone.parse_log(log, 0)
        self.assertEqual((result.to_remote, result.to_local, result.unknown), (1, 1, 0))

    def test_resync_of_initialized_pair_keeps_access_check_on_dryrun(self):
        folder = dict(self.config["folders"][0], initialized=True)
        command = rclone.bisync_command(self.paths, self.config, folder,
                                        self.paths.logs / "test.log", resync=True, dry_run=True)
        self.assertIn("--check-access", command)

    def test_exit_code_policy_and_recovery(self):
        self.prepare(initialized=True)
        rclone.apply_result(self.store, "docs", rclone.RunResult(7))
        self.assertTrue(self.store.read()["folders"]["docs"]["needs_resync"])
        rclone.apply_result(self.store, "docs", rclone.RunResult(0))
        item = self.store.read()["folders"]["docs"]
        self.assertFalse(item["needs_resync"])
        self.assertEqual(item["status"], "ok")
        self.assertIsNotNone(datetime.fromisoformat(item["last_success"]).tzinfo)
        rclone.apply_result(self.store, "docs", rclone.RunResult(5))
        self.assertFalse(self.store.read()["folders"]["docs"]["needs_resync"])

    def test_delete_safety_gets_dedicated_block(self):
        self.prepare(initialized=True)
        rclone.apply_result(self.store, "docs", rclone.RunResult(7, blocked_delete=True))
        self.assertEqual(self.store.read()["folders"]["docs"]["status"], "blocked_delete")

    def test_delete_abort_does_not_clear_preexisting_resync_requirement(self):
        self.prepare(initialized=True)
        self.store.folder("docs", status="error", needs_resync=True)
        rclone.apply_result(self.store, "docs", rclone.RunResult(7, blocked_delete=True))
        self.assertTrue(self.store.read()["folders"]["docs"]["needs_resync"])

    def test_dry_run_age_hash_and_pair(self):
        item = {"dry_run_ok_at": datetime.now(timezone.utc).isoformat(),
                "dry_run_filters_sha256": "filters", "dry_run_pair_sha256": "pair"}
        self.assertTrue(rclone.dry_run_valid(item, "filters", "pair"))
        self.assertFalse(rclone.dry_run_valid(item, "changed", "pair"))
        self.assertFalse(rclone.dry_run_valid(item, "filters", "other"))
        for value in (None, "bad", datetime.now().isoformat(),
                      (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
                      (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()):
            item["dry_run_ok_at"] = value
            self.assertFalse(rclone.dry_run_valid(item, "filters", "pair"))

    def test_marker_preserved_and_symlinks_refused(self):
        folder = self.config["folders"][0]
        marker = rclone.create_check(folder)
        self.assertEqual(marker.read_text(), i18n.CHECK_TEXT)
        marker.write_text("existing")
        rclone.create_check(folder)
        self.assertEqual(marker.read_text(), "existing")
        marker.unlink()
        marker.symlink_to(self.root / "do-not-write")
        with self.assertRaises(ValueError):
            rclone.create_check(folder)
        self.assertFalse((self.root / "do-not-write").exists())

    def test_missing_or_symlink_root_is_refused(self):
        folder = dict(self.config["folders"][0], local=str(self.root / "missing"))
        with self.assertRaises(ValueError):
            rclone.check_local(folder)
        folder["local"] = str(self.root / "link")
        Path(folder["local"]).symlink_to(self.config["folders"][0]["local"])
        with self.assertRaises(ValueError):
            rclone.check_local(folder)

    def test_network_classification_excludes_auth_and_permissions(self):
        self.assertTrue(rclone.is_network_error("dial tcp: network is unreachable"))
        self.assertFalse(rclone.is_network_error("invalid_grant: token expired"))
        self.assertFalse(rclone.is_network_error("permission denied"))

    def test_run_log_retention(self):
        for _ in range(203):
            rclone.new_log(self.paths, "docs")
        self.assertEqual(len(list(self.paths.logs.glob("*.log"))), 200)

    def test_queue_coalesces_reasons_and_serializes(self):
        queue = SyncQueue()
        self.assertTrue(queue.enqueue("a", "poll"))
        self.assertFalse(queue.enqueue("a", "watch"))
        self.assertEqual(queue.pop(), ("a", {"poll", "watch"}))
        self.assertFalse(queue.enqueue("a", "manual"))
        self.assertTrue(queue.enqueue("b", "poll"))
        self.assertIsNone(queue.pop())
        queue.finish()
        self.assertEqual(queue.pop()[0], "b")

    def test_credentials_redacted(self):
        self.assertNotIn("secret", rclone.redact('ERROR access_token="secret"'))
        self.assertNotIn("secret", rclone.redact("Authorization: Bearer secret"))
