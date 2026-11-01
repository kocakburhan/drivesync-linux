from datetime import datetime
from unittest.mock import patch
from support import IsolatedTest
from drivesync import rclone

# Captured from test_integration_rclone.py's local/local bisync (rclone 1.75.2).
REAL_CONFLICT = '''2026/11/01 02:11:56 NOTICE: - WARNING           New or changed in both paths                - nested/conflict report.txt
2026/11/01 02:11:56 INFO  : nested/conflict report.txt: Path2 is newer. Path1: 2023-11-15 01:13:30 +0300 +03, Path2: 2023-11-15 01:13:40 +0300 +03, Difference: 10s
2026/11/01 02:11:56 INFO  : nested/conflict report.txt: The winner is: Path2
2026/11/01 02:11:56 NOTICE: - Path1             Deleting Path1 copy                         - local{HK82T}:/tmp/example/local/nested/conflict report.txt
2026/11/01 02:11:56 INFO  : nested/conflict report.txt: Deleted
2026/11/01 02:11:56 INFO  : - Path2             Queue copy to Path1                         - nested/conflict report.txt
2026/11/01 02:11:56 INFO  : nested/conflict report.txt: Copied (new)
2026/11/01 02:11:56 INFO  : Bisync successful
'''


class ConflictTests(IsolatedTest):
    def parse(self, text=REAL_CONFLICT, code=0, **kwargs):
        log = self.root / "conflicts.log"
        log.write_text(text)
        return rclone.parse_log(log, code, **kwargs)

    def test_real_format_path_time_and_winning_side(self):
        result = self.parse()
        self.assertEqual(result.conflicts, 1)
        entry, = result.resolved_conflicts
        self.assertEqual(entry["path"], "nested/conflict report.txt")
        self.assertEqual(entry["winner"], "remote")
        self.assertIsNotNone(datetime.fromisoformat(entry["time"]).tzinfo)
        self.assertEqual(self.parse(REAL_CONFLICT.replace("The winner is: Path2", "The winner is: Path1"))
                         .resolved_conflicts[0]["winner"], "local")

    def test_duplicates_unknown_winner_and_filename_separators(self):
        warning = 'NOTICE: - WARNING New or changed in both paths - a: b - ü.txt\n'
        result = self.parse(warning + warning)
        self.assertEqual(result.conflicts, 1)
        self.assertEqual(result.resolved_conflicts[0]["path"], "a: b - ü.txt")
        self.assertIsNone(result.resolved_conflicts[0]["winner"])
        result = self.parse(warning + 'INFO : a: b - ü.txt: The winner is: Path1\n')
        self.assertEqual(result.resolved_conflicts[0]["winner"], "local")

    def test_failure_safety_abort_and_general_messages_are_not_resolutions(self):
        self.assertFalse(self.parse(dry_run=True).resolved_conflicts)
        self.assertFalse(self.parse(code=7).resolved_conflicts)
        self.assertFalse(self.parse(REAL_CONFLICT + 'ERROR : Safety abort: too many deletes (>25%)\n')
                         .resolved_conflicts)
        self.assertFalse(self.parse('INFO : conflict.txt: Copied (new)\nINFO : Resolved 2 conflicts\n')
                         .resolved_conflicts)

    def test_cli_preview_does_not_record_history_then_real_run_does(self):
        self.settings(dry_run={"log": REAL_CONFLICT}, bisync={"log": REAL_CONFLICT})
        self.assertEqual(self.cli("init", "docs", "--dry-run")[0], 0)
        self.assertEqual(self.store.read()["recent_conflicts"], [])
        with patch("drivesync.notify.Notifier.folder"):
            code, _, error = self.cli("init", "docs", "--yes", "--foreground")
        self.assertEqual(code, 0, error)
        entry, = self.store.read()["recent_conflicts"]
        self.assertEqual(entry["folder_local"], self.config["folders"][0]["local"])

    def test_atomic_history_bounded_persistent_and_clear_keeps_folder_state(self):
        result = self.parse()
        rclone.apply_result(self.store, "docs", result, folder=self.config["folders"][0])
        value = self.store.read()
        entry, = value["recent_conflicts"]
        self.assertEqual(entry["folder"], "docs")
        self.assertEqual(entry["folder_name"], "Documents")
        self.assertEqual(entry["folder_local"], self.config["folders"][0]["local"])
        self.store.update(lambda value: value.update(recent_conflicts=[dict(entry, path=str(n)) for n in range(100)]))
        rclone.apply_result(self.store, "docs", result, folder=self.config["folders"][0])
        value = self.store.read()
        self.assertEqual(len(value["recent_conflicts"]), 100)
        self.assertEqual(value["recent_conflicts"][0]["path"], "1")
        self.assertEqual(value["recent_conflicts"][-1]["path"], entry["path"])
        folders, daemon = value["folders"], value["daemon"]
        self.store.clear_conflicts()
        after = self.store.read()
        self.assertEqual(after["recent_conflicts"], [])
        self.assertEqual(after["folders"], folders)
        self.assertEqual(after["daemon"], daemon)
        self.assertFalse(self.calls())

    def test_old_state_defaults_to_empty_history(self):
        self.store.folder("docs", status="ok")
        self.store.update(lambda value: value.pop("recent_conflicts"))
        self.assertEqual(self.store.read()["recent_conflicts"], [])
