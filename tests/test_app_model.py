from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import patch

from support import IsolatedTest
from drivesync import app_model, config, i18n
from drivesync.presentation import present


class AppModelTests(IsolatedTest):
    def test_recent_conflicts_visible_only_with_history(self):
        for status in (None, {}, {"recent_conflicts": []}):
            with self.subTest(status=status):
                self.assertFalse(app_model.recent_conflicts_visible(status))
        for count in (1, 5):
            self.assertTrue(app_model.recent_conflicts_visible(
                {"recent_conflicts": [{"path": str(i)} for i in range(count)]}))

    def test_sync_activity_includes_requests_and_status_from_any_source(self):
        self.assertTrue(app_model.sync_busy({}, {"docs"}))
        for state in ("queued", "syncing"):
            self.assertTrue(app_model.sync_busy({"folders": {
                "other": {"status": "ok"}, "docs": {"status": state}}}))
        for state in ("ok", "error", "idle", "paused"):
            self.assertFalse(app_model.sync_busy({"folders": {"docs": {"status": state}}}))
        self.assertFalse(app_model.sync_busy(None, {"docs"}))
        self.assertFalse(app_model.sync_busy({}))

    def add(self, data=None, local="/new", remote="Yeni", **kwargs):
        snapshot = dict(exists=True, symlink=False, canonical=local,
                        configured_roots=[f["local"] for f in self.config["folders"]])
        snapshot.update(kwargs)
        return app_model.new_folder(data or self.config, local, remote, **snapshot)

    def test_add_is_uninitialized_unique_and_preserves_extensions_without_io(self):
        self.config["custom"] = {"preserve": True}
        self.config["folders"][0]["id"] = "yeni"
        before = deepcopy(self.config)
        # All inspected path facts are explicit inputs; validation does no IO.
        with patch("pathlib.Path.stat", side_effect=AssertionError("filesystem accessed")):
            result = self.add()
        self.assertEqual(self.config, before)
        self.assertEqual(result["custom"], self.config["custom"])
        self.assertEqual(result["folders"][-1], dict(id="yeni-2", local="/new", remote_name="Yeni",
                                                    enabled=True, initialized=False))

    def test_missing_file_and_symlink_root_rejected(self):
        for facts in (dict(exists=False), dict(symlink=True)):
            with self.subTest(facts=facts), self.assertRaises(config.ConfigError):
                self.add(**facts)

    def test_overlap_checks_include_disabled_roots_and_canonical_aliases(self):
        root = self.config["folders"][0]["local"]
        self.config["folders"][0]["enabled"] = False
        for path in (root, root + "/inside", str(self.root), "/"):
            with self.subTest(path=path), self.assertRaises(config.ConfigError):
                self.add(canonical=path)
        self.assertEqual(len(self.add(local=root + "-other")["folders"]), 2)

    def test_remote_name_safety_and_uniqueness(self):
        for name in ("", " ", ".", "..", "a/b", "a\\b", "a:b", "a\nb", "a\x7fb", " a", "a ", "Documents"):
            with self.subTest(name=name), self.assertRaises(config.ConfigError):
                self.add(remote=name)
        self.assertEqual(self.add(remote="Yeni Türkçe")["folders"][-1]["remote_name"], "Yeni Türkçe")

    def test_remove_only_edits_config_preserves_source_and_files(self):
        marker = self.root / "Documents" / "keep.txt"
        marker.write_text("keep")
        original = deepcopy(self.config)
        result = app_model.remove_folder(self.config, "docs")
        self.assertEqual(result["folders"], [])
        self.assertEqual(self.config, original)
        self.assertEqual(marker.read_text(), "keep")

    def test_settings_edit_preserves_latest_folders_pause_and_extensions(self):
        self.config.update(paused=True, extension=123)
        result = app_model.settings_edit(self.config, 15, 3.5, 20, [".git/", "*.tmp"])
        self.assertEqual(result["poll_interval_minutes"], 15)
        self.assertEqual(result["debounce_seconds"], 3.5)
        self.assertTrue(result["paused"])
        self.assertEqual(result["extension"], 123)
        self.assertEqual(result["folders"], self.config["folders"])
        self.assertEqual(self.config["poll_interval_minutes"], 5)
        for poll, debounce, maximum, excludes in ((0, 10, 25, []), (5, -1, 25, []),
                                                  (5, 10, 101, []), (5, 10, 25, [""])):
            with self.assertRaises(config.ConfigError):
                app_model.settings_edit(self.config, poll, debounce, maximum, excludes)

    def test_settings_edit_conflict_policy(self):
        self.assertEqual(app_model.settings_edit(self.config, 5, 10, 25, [])["conflict_policy"], "newer")
        edited = app_model.settings_edit(self.config, 5, 10, 25, [], conflict_policy="keep_both")
        self.assertEqual(edited["conflict_policy"], "keep_both")
        self.assertEqual(app_model.settings_edit(edited, 5, 10, 25, [])["conflict_policy"], "keep_both")
        with self.assertRaises(config.ConfigError):
            app_model.settings_edit(self.config, 5, 10, 25, [], conflict_policy="bogus")

    def test_rows_express_init_resync_delete_block_and_relative_success(self):
        folder = self.config["folders"][0]
        row = app_model.folder_row(folder, {"status": "ok"})
        self.assertIn("Documents ↔ Linux-PC/Documents", row.title)
        self.assertEqual(row.hint, i18n.INIT_HINT)
        self.assertFalse(row.can_sync)
        folder = dict(folder, initialized=True, remote_path="test:Linux-PC/Özel")
        clock = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
        row = app_model.folder_row(folder, dict(status="ok", last_success="2026-11-01T11:58:00+00:00"), clock)
        self.assertIn("2 dk önce", row.subtitle)
        self.assertTrue(row.can_sync)
        self.assertEqual(row.icon, "ok")
        for state in (dict(status="blocked_delete"), dict(status="error", needs_resync=True)):
            row = app_model.folder_row(folder, state)
            self.assertFalse(row.can_sync)
            self.assertEqual(row.icon, "error")
        self.assertIn(i18n.RESYNC_HINT, row.hint)

    def test_banner_explains_each_problem_and_clears_on_recovery(self):
        status = dict(folder_config=[dict(id=x) for x in "abc"], folders={
            "a": dict(status="error"), "b": dict(status="blocked_delete"), "c": dict(needs_resync=True)})
        text = app_model.problem_summary(status)
        for message in (i18n.SYNC_ERROR, i18n.DELETE_BLOCKED, i18n.BANNER_RESYNC):
            self.assertIn(message, text)
        self.assertEqual(app_model.problem_summary({}), "")

    def test_cli_syncing_rows_and_shared_window_tray_status(self):
        for initialized in (False, True):
            folder = dict(self.config["folders"][0], initialized=initialized)
            state = dict(status="syncing", needs_resync=True)
            row = app_model.folder_row(folder, state)
            expected = "Eşitleniyor" if initialized else "İlk eşitleme sürüyor"
            self.assertTrue(row.subtitle.startswith(expected + " ·"))
            self.assertEqual(row.icon, "syncing")
            self.assertEqual(row.hint, "")
            self.assertFalse(row.can_sync)
            status = dict(daemon=dict(status="idle"), folder_config=[folder], folders={"docs": state})
            self.assertEqual(app_model.overall_status(status), present(status).text)

    def test_mixed_header_and_tray_agree_without_claiming_everything_current(self):
        status = dict(folder_config=[dict(id="docs", initialized=True)] +
                      [dict(id=str(i), initialized=False) for i in range(5)] +
                      [dict(id="disabled", initialized=False, enabled=False)],
                      folders={"docs": dict(status="ok")})
        self.assertEqual(app_model.overall_status(status),
                         "✓ 1 klasör güncel · 5 klasör ilk eşitleme bekliyor")
        self.assertEqual(app_model.overall_status(status), present(status).text)

    def test_overall_status_reuses_daemon_precedence_and_turkish_times(self):
        self.assertEqual(app_model.overall_status(None), i18n.TRAY_STOPPED)
        self.assertEqual(app_model.overall_status({}), "İlk eşitleme bekliyor")
        for state, text in (("offline", "Çevrimdışı"), ("paused", "Duraklatıldı")):
            self.assertEqual(app_model.overall_status({"daemon": {"status": state}}), text)
        self.assertEqual(app_model.overall_status(dict(folder_config=[dict(id="a", initialized=True)],
                                                     folders={"a": {"status": "ok"}})),
                         "✓ Her şey güncel · Son eşitleme: henüz yok")
