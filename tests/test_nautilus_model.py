import json
from pathlib import Path
from unittest.mock import patch

from support import IsolatedTest
from drivesync import config, nautilus_model as model


class NautilusModelTests(IsolatedTest):
    def setUp(self):
        super().setUp()
        self.folder = dict(self.config["folders"][0], initialized=True)
        self.config["folders"] = [self.folder]
        self.path = Path(self.folder["local"])
        self.state = {"folders": {"docs": {"status": "ok", "last_success": "2026-11-01T12:00:00+00:00"}}}
        self.success = 1793534400.0

    def emblem(self, path=None, directory=False, mtime=None):
        return model.emblem_for(str(path or self.path / "file.txt"), directory,
                                self.success if mtime is None else mtime, self.config, self.state)

    def test_mapping_uses_path_components_and_longest_root(self):
        self.assertEqual(model.folder_for_path(str(self.path), [self.folder]), (self.folder, "."))
        self.assertEqual(model.folder_for_path(str(self.path) + "-other/a", [self.folder]), (None, None))
        nested = dict(self.folder, id="nested", local=str(self.path / "sub"))
        self.assertEqual(model.folder_for_path(str(self.path / "sub/a"), [self.folder, nested]), (nested, "a"))
        self.assertEqual(model.folder_for_path(str(self.path / "../outside"), [self.folder]), (None, None))

    def test_status_priority_and_mtime(self):
        self.assertEqual(self.emblem(), model.OK)
        self.assertEqual(self.emblem(mtime=self.success + 1), model.SYNC)
        for status in ("syncing", "queued"):
            self.state["folders"]["docs"]["status"] = status
            self.assertEqual(self.emblem(), model.SYNC)
        for status in ("error", "blocked_delete"):
            self.state["folders"]["docs"]["status"] = status
            self.assertEqual(self.emblem(), model.ERROR)
        self.state["folders"]["docs"].update(status="syncing", needs_resync=True)
        self.assertEqual(self.emblem(), model.ERROR)

    def test_uninitialized_disabled_unknown_and_missing_success(self):
        self.folder["initialized"] = False
        self.state["folders"]["docs"]["status"] = "error"
        self.assertIsNone(self.emblem())
        self.folder.update(initialized=True, enabled=False)
        self.assertIsNone(self.emblem())
        self.folder["enabled"] = True
        self.state["folders"]["docs"] = {}
        self.assertIsNone(self.emblem())
        self.assertIsNone(self.emblem(self.root / "outside"))
        self.state["folders"]["docs"] = {"last_success": "2026-11-01T12:00:00"}
        self.assertIsNone(self.emblem())

    def test_excludes_at_any_depth_and_marker_exception(self):
        for relative in ("node_modules/pkg/a.js", "src/.git/config", "a/__pycache__/b.pyc",
                         "file.tmp", "src/.~lock.report", "a/~$report.docx", "b/file~"):
            self.assertIsNone(self.emblem(self.path / relative), relative)
        self.assertIsNone(self.emblem(self.path / "src/node_modules", directory=True))
        self.config["excludes"] = ["/root-only/", "**/secret?.txt", "*"]
        self.assertIsNone(self.emblem(self.path / "a.txt"))
        self.assertEqual(self.emblem(self.path / ".drivesync-check"), model.OK)
        self.config["excludes"] = ["/root-only/", "**/secret?.txt"]
        self.assertIsNone(self.emblem(self.path / "root-only/a"))
        self.assertEqual(self.emblem(self.path / "nested/root-only/a"), model.OK)
        self.assertIsNone(self.emblem(self.path / "nested/secret1.txt"))

    def test_search_url_encodes_filename(self):
        self.assertEqual(model.drive_search_url("rapor #1 & ü.txt"),
                         "https://drive.google.com/drive/search?q=rapor+%231+%26+%C3%BC.txt")

    def test_unsupported_exclude_syntax_suppresses_badges(self):
        self.config["excludes"] = ["{secret,private}/**"]
        self.assertIsNone(self.emblem(self.path / "secret/a.txt"))
        self.assertIsNone(self.emblem(self.path / "other.txt"))

    def test_cache_throttles_and_reuses_mtime_then_discards_bad_input(self):
        clock = [0.0]
        config.save(self.paths, self.config)
        self.paths.state.write_text(json.dumps(self.state))
        cache = model.SnapshotCache(self.paths, lambda: clock[0])
        first = cache.read()
        self.assertEqual(first, (self.config, self.state))
        self.paths.state.write_text("{broken")
        self.assertEqual(cache.read(), first)
        clock[0] = 1
        self.assertEqual(cache.read(), (self.config, {}))
        self.paths.state.write_text(json.dumps(self.state))
        clock[0] = 2
        cache.read()
        with patch.object(Path, "open", side_effect=AssertionError("unchanged JSON reread")):
            clock[0] = 3
            self.assertEqual(cache.read(), first)
        self.paths.config.unlink()
        clock[0] = 4
        self.assertEqual(cache.read()[0], {})
        self.assertFalse(self.paths.config.exists())

    def test_cache_no_stat_or_read_more_than_once_per_second(self):
        cache = model.SnapshotCache(self.paths, lambda: 0)
        cache.read()
        with patch.object(Path, "stat", side_effect=AssertionError("stat repeated")):
            self.assertEqual(cache.read()[1], {})
        self.assertFalse(self.calls())
