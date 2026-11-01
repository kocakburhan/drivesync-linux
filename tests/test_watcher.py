from pathlib import Path
from unittest.mock import Mock

from support import IsolatedTest
from drivesync import config, filters
from drivesync.watcher import Debouncer, FolderWatchers


class Scheduler:
    def __init__(self):
        self.now, self.next_id = 0, 0
        self.tasks = {}

    def schedule(self, milliseconds, callback):
        self.next_id += 1
        self.tasks[self.next_id] = (self.now + milliseconds / 1000, callback)
        return self.next_id

    def cancel(self, handle):
        del self.tasks[handle]

    def advance(self, seconds):
        target = self.now + seconds
        while self.tasks:
            handle = min(self.tasks, key=lambda key: self.tasks[key][0])
            when, callback = self.tasks[handle]
            if when > target:
                break
            del self.tasks[handle]
            self.now = when
            callback()
        self.now = target


class FakeFile:
    def __init__(self, path):
        self.path = str(path)

    def get_path(self):
        return self.path


class FakeMonitor:
    def __init__(self, path):
        self.path = path
        self.cancelled = False

    def connect(self, signal, callback):
        self.callback = callback

    def cancel(self):
        self.cancelled = True

    def emit(self, path, other=None):
        self.callback(self, FakeFile(path), FakeFile(other) if other else None, None)


class WatcherTests(IsolatedTest):
    def setUp(self):
        super().setUp()
        self.scheduler = Scheduler()
        self.enqueue = Mock()
        self.warning = Mock()
        self.watchers = FolderWatchers(self.enqueue, self.scheduler.schedule, self.scheduler.cancel,
            monitor_factory=FakeMonitor, clock=lambda: self.scheduler.now, warn=self.warning)
        self.addCleanup(self.watchers.close)
        self.folder = self.config["folders"][0]
        self.folder["initialized"] = True
        self.local = Path(self.folder["local"])

    def configure(self):
        self.watchers.configure(self.config)
        return self.watchers.monitors.get("docs", {}).get(self.local)

    def test_trailing_debounce_restarts_and_merges_bursts(self):
        monitor = self.configure()
        monitor.emit(self.local / "a.txt")
        self.scheduler.advance(9)
        monitor.emit(self.local / "b.txt")
        self.scheduler.advance(9)
        self.enqueue.assert_not_called()
        self.scheduler.advance(1)
        self.enqueue.assert_called_once_with("docs", "local-change")
        self.assertFalse(self.scheduler.tasks)

    def test_begin_cancels_pending_and_run_plus_five_seconds_are_ignored(self):
        monitor = self.configure()
        monitor.emit(self.local / "pending")
        self.watchers.begin("docs")
        self.assertFalse(self.watchers.debounce.pending)
        self.scheduler.advance(20)
        monitor.emit(self.local / "own-write")
        self.watchers.end("docs")
        self.scheduler.advance(4.999)
        monitor.emit(self.local / "late-own-write")
        self.enqueue.assert_not_called()
        self.scheduler.advance(.001)
        monitor = self.watchers.monitors["docs"][self.local]
        monitor.emit(self.local / "user-write")
        self.scheduler.advance(10)
        self.enqueue.assert_called_once_with("docs", "local-change")

    def test_recursive_excludes_symlinks_and_dynamic_directories(self):
        for name in ("normal/deep", "node_modules/deep", ".git/objects", "normal/build/deep"):
            (self.local / name).mkdir(parents=True)
        (self.local / "link").symlink_to(self.local / "normal", target_is_directory=True)
        monitor = self.configure()
        self.assertEqual(set(self.watchers.monitors["docs"]),
                         {self.local, self.local / "normal", self.local / "normal/deep"})
        new = self.local / "new/subdirectory"
        new.mkdir(parents=True)
        monitor.emit(new.parent)
        self.assertIn(new, self.watchers.monitors["docs"])
        monitor.emit(self.local / "node_modules/deep/file")
        self.scheduler.advance(10)
        self.enqueue.assert_called_once_with("docs", "local-change")

    def test_excluded_file_and_directory_events_never_enqueue(self):
        (self.local / "node_modules").mkdir()
        monitor = self.configure()
        for path in ("node_modules", "node_modules/file", "scratch.tmp", "file.swp", "backup~"):
            monitor.emit(self.local / path)
        self.scheduler.advance(20)
        self.enqueue.assert_not_called()

    def test_moves_into_tree_are_scanned_and_moves_out_cancel_old_monitors(self):
        monitor = self.configure()
        (self.local / "moved/deep").mkdir(parents=True)
        monitor.emit(self.root / "outside", self.local / "moved")
        old = self.watchers.monitors["docs"][self.local / "moved/deep"]
        (self.local / "moved").rename(self.root / "outside")
        monitor.emit(self.local / "moved", self.root / "outside")
        self.assertTrue(old.cancelled)
        self.assertEqual(self.watchers.count, 1)

    def test_global_cap_falls_back_only_affected_folder_and_warns_once(self):
        (self.local / "nested").mkdir()
        second = self.root / "second"
        second.mkdir()
        self.config["folders"].append(dict(self.folder, id="second", local=str(second), remote_name="Second"))
        self.config["max_monitors"] = 2
        self.configure()
        self.assertEqual(self.watchers.count, 2)
        self.assertEqual(self.watchers.fallback, {"second"})
        self.warning.assert_called_once()
        self.config["debounce_seconds"] = 11
        self.configure()
        self.assertEqual(self.warning.call_count, 1)

    def test_dynamic_cap_cancels_partial_monitors_and_debounce(self):
        self.config["max_monitors"] = 2
        monitor = self.configure()
        monitor.emit(self.local / "file")
        (self.local / "new/deep").mkdir(parents=True)
        monitor.emit(self.local / "new")
        self.assertEqual(self.watchers.fallback, {"docs"})
        self.assertTrue(monitor.cancelled)
        self.assertEqual(self.watchers.count, 0)
        self.scheduler.advance(20)
        self.enqueue.assert_not_called()
        self.warning.assert_called_once()

    def test_monitor_error_falls_back_without_crashing(self):
        self.watchers.factory = Mock(side_effect=OSError("watch limit"))
        self.configure()
        self.assertEqual(self.watchers.fallback, {"docs"})
        self.warning.assert_called_once()

    def test_rescan_after_own_run_adds_downloaded_dirs_without_sync_loop(self):
        self.configure()
        self.watchers.begin("docs")
        (self.local / "downloaded/deep").mkdir(parents=True)
        self.watchers.end("docs")
        self.assertEqual(self.watchers.count, 1)
        self.scheduler.advance(5)
        self.assertEqual(self.watchers.count, 3)
        self.enqueue.assert_not_called()

    def test_reconfigure_removes_disabled_uninitialized_and_deleted_folders(self):
        for mode in ("disabled", "uninitialized", "removed"):
            with self.subTest(mode=mode):
                self.config["folders"] = [dict(self.folder, enabled=True, initialized=True)]
                monitor = self.configure()
                monitor.emit(self.local / "file")
                if mode == "removed":
                    self.config["folders"] = []
                else:
                    self.config["folders"][0]["enabled" if mode == "disabled" else "initialized"] = False
                self.configure()
                self.assertTrue(monitor.cancelled)
                self.assertFalse(self.watchers.monitors)
                self.scheduler.advance(20)
                self.enqueue.assert_not_called()

    def test_unchanged_config_retains_monitors_and_pending_event(self):
        monitor = self.configure()
        monitor.emit(self.local / "user-write")
        self.assertIs(monitor, self.configure())
        self.scheduler.advance(10)
        self.enqueue.assert_called_once()

    def test_root_symlink_is_never_watched(self):
        alias = self.root / "alias"
        alias.symlink_to(self.local, target_is_directory=True)
        self.folder["local"] = str(alias)
        self.configure()
        self.assertEqual(self.watchers.count, 0)

    def test_watch_limit_config_validated_and_defaults_backfilled(self):
        data = dict(self.config)
        data.pop("max_monitors")
        self.assertEqual(config.validate(data)["max_monitors"], 20000)
        for value in (0, -1, True, 1.5, "20000"):
            with self.subTest(value=value), self.assertRaises(config.ConfigError):
                config.validate(dict(data, max_monitors=value))

    def test_exclude_globs_do_not_cross_single_star_slashes(self):
        cases = [("a/node_modules/child", True, ["node_modules/"], True),
                 ("top/a/sub/bar", True, ["top/*/bar/"], False),
                 ("top/a/bar", True, ["top/*/bar/"], True),
                 ("top/a/bar", True, ["**/bar/"], True),
                 ("bar", True, ["**/bar/"], True),
                 ("top/.git", True, ["/.git/"], False),
                 (".git", True, ["/.git/"], True),
                 ("node_modules", False, ["node_modules/"], False),
                 (".drivesync-check", False, ["*"], False),
                 ("file1", False, ["file[0-9]"], True)]
        for path, directory, patterns, expected in cases:
            with self.subTest(path=path, patterns=patterns):
                self.assertEqual(filters.excluded(path, directory, patterns), expected)
