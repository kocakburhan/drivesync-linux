import json
from unittest.mock import patch

from support import IsolatedTest
from drivesync import app_model, config, i18n, presentation, rclone
from drivesync.daemon import Daemon
from drivesync.notify import Notifier
from test_daemon import drain_until

VALID = ["", "1M", "500k", "1.5M", "off", "10M:off", "off:2M", "1Mi", "2MiB", "100",
         "08:00,1M 23:00,off", "Mon-08:00,512k:2M 22:00,off", "00:00,off"]
INVALID = ["x", "1X", "M", "1M:", ":1M", "10M:off:1M", "25:00,1M", "08:60,1M", "8:00,1M",
           "08:00,", "Foo-08:00,1M", "1M 2M", "--bwlimit", "-1M", " 1M", "1M ", "1M\n",
           "08:00,1M;rm", "1M $(x)", "a" * 201, "08:00,1M,23:00,off"]


class FakePower:
    def __init__(self, metered=False, on_battery=False):
        self.metered, self.on_battery, self.callback = metered, on_battery, None

    def set(self, **values):
        self.__dict__.update(values)
        self.callback()

    def close(self):
        pass


class BandwidthTests(IsolatedTest):
    def test_validation(self):
        for value in VALID:
            with self.subTest(value=value):
                self.assertTrue(config.valid_bwlimit(value))
                config.validate(dict(self.config, bwlimit=value))
        for value in INVALID + [None, 5, ["1M"]]:
            with self.subTest(value=value):
                self.assertFalse(config.valid_bwlimit(value))
                with self.assertRaises(config.ConfigError):
                    config.validate(dict(self.config, bwlimit=value))

    def test_defaults_and_flag_types(self):
        defaults = config.defaults()
        self.assertEqual((defaults["bwlimit"], defaults["pause_on_battery"],
                          defaults["pause_on_metered"], defaults["pause_stops_running"]),
                         ("", False, True, False))
        for key in ("pause_on_battery", "pause_on_metered", "pause_stops_running"):
            with self.assertRaises(config.ConfigError):
                config.validate(dict(self.config, **{key: 1}))

    def test_command_arguments(self):
        folder = self.config["folders"][0]
        log = self.paths.logs / "run.log"
        for limit in ("", "08:00,1M 23:00,off"):
            for kwargs in ({}, dict(resync=True), dict(resync=True, dry_run=True), dict(force=True)):
                with self.subTest(limit=limit, **kwargs):
                    command = rclone.bisync_command(self.paths, dict(self.config, bwlimit=limit),
                                                    folder, log, **kwargs)
                    if limit:
                        self.assertEqual(command[command.index("--bwlimit") + 1], limit)
                        self.assertEqual(command.count("--bwlimit"), 1)
                    else:
                        self.assertNotIn("--bwlimit", command)

    def test_changing_limit_does_not_invalidate_approval(self):
        folder = self.config["folders"][0]
        self.assertEqual(config.pair_hash(self.config, folder),
                         config.pair_hash(dict(self.config, bwlimit="1M"), folder))

    def test_cli_config_set(self):
        with patch("drivesync.cli.dbus_service.call", return_value=True) as call:
            code, out, _ = self.cli("config", "set", "bwlimit", "08:00,1M 23:00,off", "--json")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["value"], "08:00,1M 23:00,off")
            self.assertEqual(config.load(self.paths)["bwlimit"], "08:00,1M 23:00,off")
            self.assertEqual(self.cli("config", "set", "bwlimit", "", "--json")[0], 0)
            self.assertEqual(config.load(self.paths)["bwlimit"], "")
            for key, value in (("pause_on_battery", "true"), ("pause_on_metered", "false"),
                               ("pause_stops_running", "true")):
                self.assertEqual(self.cli("config", "set", key, value, "--json")[0], 0)
                self.assertIs(config.load(self.paths)[key], value == "true")
            self.assertEqual(call.call_count, 5)
        before = self.paths.config.read_bytes()
        for key, value in (("bwlimit", "bogus"), ("pause_on_battery", "1"), ("pause_on_metered", "null")):
            self.assertEqual(self.cli("config", "set", key, value, "--json")[0], 1)
            self.assertEqual(self.paths.config.read_bytes(), before)

    def test_settings_edit(self):
        edited = app_model.settings_edit(self.config, 5, 10, 25, [], bwlimit=" 1M ",
                                         pause_on_battery=True, pause_on_metered=False)
        self.assertEqual((edited["bwlimit"], edited["pause_on_battery"], edited["pause_on_metered"]),
                         ("1M", True, False))
        with self.assertRaises(config.ConfigError):
            app_model.settings_edit(self.config, 5, 10, 25, [], bwlimit="nonsense")
        self.assertEqual(app_model.settings_edit(self.config, 5, 10, 25, [])["bwlimit"], "")


class AutoPauseTests(IsolatedTest):
    def daemon(self, power, **overrides):
        self.config.update(overrides)
        config.save(self.paths, self.config)
        self.prepare(initialized=True)
        with patch("drivesync.daemon.Notifier", return_value=Notifier(send=lambda *_: None)):
            daemon = Daemon(self.paths, power)
        daemon.reload_config()
        daemon.next_poll = float("inf")
        self.addCleanup(daemon.watchers.close)
        self.addCleanup(lambda: daemon.child.kill() if daemon.child else None)
        return daemon

    def bisyncs(self):
        return [c for c in self.calls() if "bisync" in c["argv"]]

    def test_metered_blocks_then_unblocks_queue(self):
        power = FakePower(metered=True)
        daemon = self.daemon(power)
        self.assertEqual(daemon.auto_reason, "metered")
        self.assertEqual(daemon._idle_status(), "auto_paused")
        self.assertTrue(daemon.sync_now("docs"))
        daemon._pump()
        daemon._tick()
        self.assertIsNone(daemon.child)
        self.assertEqual(self.calls(), [])
        self.assertEqual(daemon.store.read()["folders"]["docs"]["status"], "queued")
        status = json.loads(daemon.status_json())
        self.assertEqual((status["auto_pause"], status["paused"]), ("metered", False))
        self.assertEqual(daemon.store.read()["daemon"]["status"], "auto_paused")
        power.set(metered=False)
        self.assertIsNone(daemon.auto_reason)
        self.assertEqual(daemon.store.read()["daemon"]["status"], "idle")
        daemon._pump()
        self.assertIsNotNone(daemon.child)
        drain_until(lambda: daemon.child is None)
        self.assertEqual(daemon.store.read()["folders"]["docs"]["status"], "ok")
        self.assertEqual(len(self.bisyncs()), 1)

    def test_battery_only_when_enabled_and_metered_setting_respected(self):
        power = FakePower(metered=True, on_battery=True)
        daemon = self.daemon(power, pause_on_metered=False)
        self.assertIsNone(daemon.auto_reason)
        power.set(on_battery=True)
        self.assertIsNone(daemon.auto_reason)
        daemon = self.daemon(power, pause_on_metered=False, pause_on_battery=True)
        self.assertEqual(daemon.auto_reason, "battery")
        daemon = self.daemon(power, pause_on_metered=True, pause_on_battery=True)
        self.assertEqual(daemon.auto_reason, "metered")  # metered takes precedence
        power.set(metered=False)
        self.assertEqual(daemon.auto_reason, "battery")

    def test_setting_change_via_reload_applies_immediately(self):
        power = FakePower(on_battery=True)
        daemon = self.daemon(power)
        self.assertIsNone(daemon.auto_reason)
        data = config.load(self.paths)
        data["pause_on_battery"] = True
        config.save(self.paths, data)
        daemon.reload_config()
        self.assertEqual(daemon.auto_reason, "battery")

    def test_manual_pause_is_independent(self):
        power = FakePower(metered=True)
        daemon = self.daemon(power)
        self.assertTrue(daemon.pause(True))
        self.assertEqual(daemon.store.read()["daemon"]["status"], "paused")
        power.set(metered=False)
        self.assertEqual(daemon.store.read()["daemon"]["status"], "paused")
        self.assertTrue(daemon.config["paused"])
        self.assertFalse(daemon.sync_now("docs"))
        power.set(metered=True)
        self.assertTrue(json.loads(daemon.status_json())["paused"])
        daemon.pause(False)
        self.assertFalse(daemon.config["paused"])
        self.assertEqual(daemon.store.read()["daemon"]["status"], "auto_paused")
        power.set(metered=False)
        self.assertEqual(daemon.store.read()["daemon"]["status"], "idle")

    def test_running_transfer_finishes_by_default(self):
        self.settings(bisync=dict(log="", sleep=0.6, code=0))
        power = FakePower()
        daemon = self.daemon(power)
        daemon.sync_now("docs")
        daemon._pump()
        drain_until(lambda: daemon.child is not None)
        power.set(metered=True)
        self.assertFalse(daemon.auto_stop)
        drain_until(lambda: daemon.child is None, timeout=10)
        self.assertEqual(daemon.store.read()["folders"]["docs"]["status"], "ok")
        self.assertEqual(daemon.store.read()["daemon"]["status"], "auto_paused")

    def test_pause_stops_running_requeues_and_resumes(self):
        self.settings(bisync=dict(log="", sleep=30, code=0))
        power = FakePower()
        daemon = self.daemon(power, pause_stops_running=True)
        daemon.sync_now("docs")
        daemon._pump()
        # Interrupt during the long bisync stage, after the probe has finished.
        drain_until(lambda: len(self.bisyncs()) > 0, timeout=10)
        power.set(metered=True)
        drain_until(lambda: daemon.child is None and daemon.queue.current is None, timeout=10)
        item = daemon.store.read()["folders"]["docs"]
        self.assertNotEqual(item["status"], "error")
        self.assertIsNone(item["last_error"])
        self.assertIn("docs", daemon.queue.pending)
        self.assertFalse(daemon.auto_stop)
        daemon._pump()
        self.assertIsNone(daemon.child)
        self.settings(bisync=dict(log="", sleep=0, code=0))
        power.set(metered=False)
        daemon._pump()
        self.assertIsNotNone(daemon.child)
        drain_until(lambda: daemon.child is None, timeout=10)
        self.assertEqual(daemon.store.read()["folders"]["docs"]["status"], "ok")


class PresentationTests(IsolatedTest):
    STATUS = {"daemon": {"status": "auto_paused"}, "paused": False, "folders": {}, "folder_config": []}

    def test_reason_text_tr_en(self):
        self.addCleanup(i18n.set_language, None)
        for reason, tr, en in (("metered", "Kotalı ağda duraklatıldı", "Paused on a metered network"),
                               ("battery", "Pilde duraklatıldı", "Paused on battery")):
            status = dict(self.STATUS, auto_pause=reason)
            for language, expected in (("tr", tr), ("en", en)):
                i18n.set_language(language)
                view = presentation.present(status)
                self.assertEqual((view.text, view.icon, view.paused), (expected, "paused", False))
                self.assertEqual(app_model.overall_status(status), expected)

    def test_manual_pause_wins_and_labels(self):
        self.addCleanup(i18n.set_language, None)
        i18n.set_language("tr")
        view = presentation.present(dict(self.STATUS, auto_pause="metered", paused=True))
        self.assertEqual((view.text, view.paused), (i18n.TRAY_PAUSED, True))
        self.assertEqual(i18n.STATUSES["auto_paused"], "Otomatik duraklatıldı")
        self.assertEqual(i18n.BWLIMIT, "Bant genişliği sınırı")
