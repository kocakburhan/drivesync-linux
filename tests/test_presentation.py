from datetime import datetime, timedelta, timezone
import unittest

import support
from drivesync.presentation import ICONS, present, relative_time


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)

    def test_relative_times(self):
        for seconds, expected in ((0, "az önce"), (59, "az önce"), (60, "1 dk önce"),
                                  (180, "3 dk önce"), (7200, "2 sa önce"),
                                  (86400, "dün"), (172800, "2 gün önce"), (-10, "az önce")):
            with self.subTest(seconds=seconds):
                date = (self.now - timedelta(seconds=seconds)).isoformat()
                self.assertEqual(relative_time(date, self.now), expected)

    def test_missing_invalid_and_naive_times(self):
        for value in (None, "", "invalid", 42):
            self.assertEqual(relative_time(value, self.now), "henüz yok")
        self.assertEqual(relative_time("2026-11-01T11:57:00", self.now), "3 dk önce")
        self.assertEqual(relative_time("2026-11-01T14:57:00+03:00", self.now), "3 dk önce")

    def test_daemon_absence(self):
        view = present()
        self.assertFalse(view.available)
        self.assertEqual(view.text, "Arka plan servisi çalışmıyor")
        self.assertEqual(view.icon, "error")

    def test_statuses_and_precedence(self):
        for state, paused, text, icon in (
                ("paused", False, "Duraklatıldı", "paused"),
                ("idle", True, "Duraklatıldı", "paused"),
                ("offline", True, "Çevrimdışı", "offline"),
                ("syncing", False, "Eşitleniyor: Documents", "syncing"),
                ("error", False, "Hata", "error")):
            with self.subTest(state=state):
                view = present({"daemon": {"status": state, "current_folder": "docs"},
                                "paused": paused,
                                "folder_config": [{"id": "docs", "remote_name": "Documents"}]})
                self.assertEqual((view.text, view.icon), (text, icon))
                self.assertIn(view.icon, ICONS)

    def test_folder_errors_and_resync_count_once(self):
        view = present({"folder_config": [dict(id=k, enabled=True, initialized=True)
                                                 for k in "abcd"],
                        "folders": {"a": {"status": "error", "needs_resync": True},
                                    "b": {"status": "blocked_delete"},
                                    "c": {"needs_resync": True}, "d": {"status": "ok"}}})
        self.assertEqual(view.text, "Hata: 3 klasör")

    def test_latest_success_uses_timezones_and_ignores_invalid_dates(self):
        view = present({"folder_config": [dict(id=k, enabled=True, initialized=True)
                                                 for k in "abc"],
                        "folders": {"a": {"status": "ok", "last_success": "invalid"},
                                    "b": {"status": "ok", "last_success": "2026-11-01T14:57:00+03:00"},
                                    "c": {"status": "ok", "last_success": "2026-11-01T11:00:00+00:00"}}}, self.now)
        self.assertEqual(view.text, "Güncel · Son eşitleme: 3 dk önce")
        self.assertEqual(view.icon, "ok")
        self.assertEqual(present({}).text, "İlk eşitleme bekliyor")

    def test_no_initialized_enabled_folder_is_neutral(self):
        for enabled, initialized in ((True, False), (False, True), (False, False)):
            with self.subTest(enabled=enabled, initialized=initialized):
                view = present({"folder_config": [dict(id="a", enabled=enabled,
                                                       initialized=initialized)],
                                "folders": {"a": {"status": "error", "needs_resync": True}}})
                self.assertEqual(view.text, "İlk eşitleme bekliyor")
                self.assertEqual(view.icon, "neutral")
                self.assertIn(view.icon, ICONS)

    def test_current_counts_waiting_and_ignores_disabled_folders(self):
        status = {"folder_config": [dict(id="a", enabled=True, initialized=True),
                                    dict(id="b", enabled=False, initialized=True),
                                    dict(id="c", enabled=True, initialized=False)],
                  "folders": {"a": {"status": "ok"}, "b": {"status": "error"},
                              "c": {"status": "not_initialized"}}}
        self.assertEqual(present(status).text, "✓ 1 klasör güncel · 1 klasör ilk eşitleme bekliyor")
        self.assertEqual(present(status).icon, "neutral")
        status["folders"]["a"]["status"] = "queued"
        self.assertNotEqual(present(status).icon, "ok")

    def test_folder_syncing_is_authoritative_over_daemon_fields(self):
        for initialized in (False, True):
            for daemon_state in ("idle", "error", "paused", "offline", "syncing"):
                with self.subTest(initialized=initialized, daemon_state=daemon_state):
                    status = dict(daemon=dict(status=daemon_state, current_folder="other"),
                                  folder_config=[dict(id="pictures", remote_name="Pictures",
                                                      initialized=initialized)],
                                  folders={"pictures": dict(status="syncing", needs_resync=True)})
                    view = present(status)
                    expected = "Eşitleniyor: Pictures" if initialized else "İlk eşitleme sürüyor: Pictures"
                    self.assertEqual((view.text, view.icon), (expected, "syncing"))

    def test_syncing_folder_without_config_uses_identifier(self):
        view = present(dict(folders={"pictures": dict(status="syncing")}))
        self.assertEqual((view.text, view.icon), ("Eşitleniyor: pictures", "syncing"))
