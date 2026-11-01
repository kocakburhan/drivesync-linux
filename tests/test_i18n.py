from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import string
import unittest
from unittest.mock import patch

from support import IsolatedTest
from drivesync import i18n, presentation


def placeholders(text):
    names = [name for _, name, _, _ in string.Formatter().parse(text) if name is not None]
    return sorted(names + re.findall(r"%[-+ #0-9.]*[sdf]", text))


class TableTests(unittest.TestCase):
    def test_every_entry_has_both_languages_and_matching_placeholders(self):
        pairs = dict(i18n._STRINGS)
        for table in (i18n.HELP, i18n.STATUSES, i18n.DOCTOR_MESSAGES):
            pairs.update({f"{id(table)}:{key}": table.raw(key) for key in table})
        self.assertGreater(len(pairs), 150)
        for name, pair in pairs.items():
            self.assertEqual(len(pair), 2, name)
            tr, en = pair
            self.assertTrue(tr.strip() and en.strip(), name)
            self.assertEqual(placeholders(tr), placeholders(en), name)

    def test_language_switch_and_plain_constants(self):
        self.addCleanup(i18n.set_language)
        i18n.set_language("tr")
        self.assertEqual(i18n.SYNC_NOW, "Şimdi eşitle")
        self.assertEqual(i18n.STATUSES["ok"], "Güncel")
        i18n.set_language("en")
        self.assertEqual(i18n.SYNC_NOW, "Sync now")
        self.assertEqual(i18n.STATUSES["ok"], "Up to date")
        self.assertEqual(i18n.APP_NAME, "DriveSync")
        i18n.STATUSES["extra"] = "Only plain"
        self.addCleanup(i18n.STATUSES.__delitem__, "extra")
        self.assertEqual(i18n.STATUSES["extra"], "Only plain")
        with self.assertRaises(AttributeError):
            i18n.NO_SUCH_STRING


class SelectionTests(IsolatedTest):
    def setUp(self):
        super().setUp()
        self.addCleanup(i18n.set_language)
        i18n.set_language(None)
        for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
            patcher = patch.dict("os.environ")
            patcher.start()
            self.addCleanup(patcher.stop)
            __import__("os").environ.pop(name, None)
        __import__("os").environ.pop("DRIVESYNC_LANG", None)

    def set_config(self, value):
        path = self.paths.config_dir / "config.json"
        data = json.loads(path.read_text())
        data["language"] = value
        path.write_text(json.dumps(data))

    def setenv(self, **values):
        import os
        os.environ.update(values)

    def test_auto_follows_locale(self):
        self.assertEqual(i18n.current_language(), "en")
        self.setenv(LANG="tr_TR.UTF-8")
        self.assertEqual(i18n.current_language(), "tr")
        self.setenv(LANG="de_DE.UTF-8")
        self.assertEqual(i18n.current_language(), "en")
        self.setenv(LC_ALL="tr_TR.UTF-8")
        self.assertEqual(i18n.current_language(), "tr")
        self.setenv(LANGUAGE="en_US:tr")
        self.assertEqual(i18n.current_language(), "en")

    def test_env_overrides_locale_and_config_overrides_env(self):
        self.setenv(LANG="tr_TR.UTF-8", DRIVESYNC_LANG="en")
        self.assertEqual(i18n.current_language(), "en")
        self.set_config("tr")
        self.setenv(DRIVESYNC_LANG="en")
        self.assertEqual(i18n.current_language(), "tr")
        self.set_config("en")
        self.setenv(DRIVESYNC_LANG="tr")
        self.assertEqual(i18n.current_language(), "en")
        self.set_config("auto")
        self.assertEqual(i18n.current_language(), "tr")
        self.setenv(DRIVESYNC_LANG="auto", LANG="tr_TR.UTF-8")
        self.assertEqual(i18n.current_language(), "tr")

    def test_cli_config_language(self):
        code, out, _ = self.cli("config", "set", "language", "en", "--json")
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(out)["value"], "en")
        self.assertEqual(i18n.current_language(), "en")
        code, out, _ = self.cli("config", "get", "language", "--json")
        self.assertEqual(json.loads(out)["value"], "en")
        code, _, err = self.cli("config", "set", "language", "fr", "--json")
        self.assertEqual(code, 1)


class FormattingTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(i18n.set_language)
        self.now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)

    def ago(self, **delta):
        return presentation.relative_time((self.now - timedelta(**delta)).isoformat(), self.now)

    def test_english_relative_times(self):
        i18n.set_language("en")
        self.assertEqual(self.ago(seconds=5), "just now")
        self.assertEqual(self.ago(minutes=3), "3 min ago")
        self.assertEqual(self.ago(hours=2), "2 h ago")
        self.assertEqual(self.ago(days=1), "yesterday")
        self.assertEqual(self.ago(days=4), "4 days ago")
        self.assertEqual(presentation.relative_time(None, self.now), "never")

    def test_english_progress(self):
        i18n.set_language("en")
        progress = dict(percent=35, bytes_done=int(1.2 * 1024 ** 3), bytes_total=int(3.4 * 1024 ** 3),
                        speed=2 * 1024 ** 2, eta_seconds=5280, files_done=12, files_total=1200)
        self.assertEqual(presentation.progress_text(progress),
                         "1.2 / 3.4 GB · 35% · 2.0 MB/s · ~1 h 28 min left · 12/1,200 files")
        self.assertEqual(presentation.progress_text(progress, compact=True), "35% · ~1 h 28 min left")
        self.assertEqual(presentation.eta_text(59), "~59 s left")
        i18n.set_language("tr")
        self.assertEqual(presentation.progress_text(progress),
                         "1,2 / 3,4 GB · %35 · 2,0 MB/sn · kalan ~1 sa 28 dk · 12/1.200 dosya")


if __name__ == "__main__":
    unittest.main()
