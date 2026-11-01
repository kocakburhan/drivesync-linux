from copy import deepcopy
import json
from pathlib import Path
import time
from unittest.mock import patch

from gi.repository import Gio, GLib

from support import IsolatedTest
from drivesync import config, i18n, rclone
from drivesync.daemon import Daemon
from drivesync.notify import Notifier
from drivesync.state import execution_lock


def drain_until(predicate, timeout=5):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("GLib operation timed out")
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


class DaemonTests(IsolatedTest):
    def test_live_progress_is_published_and_cleared_on_success_and_error(self):
        from test_progress import STATS
        daemon = self.daemon()
        daemon.next_poll = float("inf")
        for code in (0, 5):
            with self.subTest(code=code):
                self.settings(bisync=dict(log=STATS + "\n", sleep=2.5, code=code))
                observed = []
                daemon.store.changed = lambda: observed.append(daemon.store.read()["folders"]["docs"])
                self.assertTrue(daemon.sync_now("docs"))
                daemon._pump()
                timer = GLib.timeout_add(25, daemon._tick)
                try:
                    drain_until(lambda: daemon.child is None)
                finally:
                    GLib.source_remove(timer)
                self.assertTrue(any(item.get("progress", {}).get("percent") == 35
                                    for item in observed if item.get("progress")))
                self.assertIsNone(daemon.store.read()["folders"]["docs"]["progress"])
                self.assertIsNone(daemon.progress)

    def test_external_cli_state_writes_trigger_status_updates(self):
        from unittest.mock import Mock
        daemon = self.daemon(initialized=False)
        daemon.service = Mock()
        monitor = Gio.File.new_for_path(str(self.paths.state_dir)).monitor_directory(
            Gio.FileMonitorFlags.NONE, None)
        self.addCleanup(monitor.cancel)
        monitor.connect("changed", daemon._state_changed)
        # This is a different store, with no daemon.changed callback attached.
        self.store.folder("docs", status="syncing")
        drain_until(lambda: daemon.service.changed.called)
        daemon.service.changed.reset_mock()
        from test_progress import STATS
        self.store.progress("docs", rclone.parse_progress(STATS))
        drain_until(lambda: daemon.service.changed.called)
        self.assertIn('"percent": 35.0', daemon.status_json())

    def daemon(self, initialized=True):
        self.prepare(initialized=initialized)
        with patch("drivesync.daemon.Notifier", return_value=Notifier(send=lambda *_: None)):
            daemon = Daemon(self.paths)
        daemon.reload_config()
        self.addCleanup(daemon.watchers.close)
        self.addCleanup(lambda: daemon.child.kill() if daemon.child else None)
        return daemon

    def test_uninitialized_and_resync_blocked_folders_never_queue(self):
        daemon = self.daemon(initialized=False)
        self.assertFalse(daemon.sync_now("docs"))
        daemon._tick()
        self.assertEqual(self.calls(), [])
        daemon.config["folders"][0]["initialized"] = True
        config.save(self.paths, daemon.config)
        daemon.store.folder("docs", needs_resync=True, status="error")
        self.assertFalse(daemon.enqueue("docs", "poll"))

    def test_delete_block_never_automatically_retries(self):
        daemon = self.daemon()
        daemon.store.folder("docs", status="blocked_delete")
        self.assertFalse(daemon.sync_now("docs"))
        daemon._tick()
        self.assertEqual(self.calls(), [])

    def test_serialized_probe_then_bisync_and_status(self):
        self.config.update(transfers=3, init_transfers=11, checkers=9)
        config.save(self.paths, self.config)
        daemon = self.daemon()
        self.assertTrue(daemon.sync_now("docs"))
        daemon._pump()
        drain_until(lambda: daemon.child is None)
        self.assertEqual([c["argv"][0] for c in self.calls()], ["lsf", "bisync"])
        command = self.calls()[-1]["argv"]
        self.assertEqual(command[command.index("--transfers") + 1], "3")
        self.assertEqual(command[command.index("--checkers") + 1], "9")
        self.assertNotIn("--drive-pacer-min-sleep", command)
        self.assertNotIn("--drive-pacer-burst", command)
        self.assertEqual(daemon.store.read()["folders"]["docs"]["status"], "ok")
        self.assertIsNone(daemon.store.read()["daemon"]["current_folder"])
        self.assertIsNone(daemon.lock)

    def test_multiple_folders_run_without_child_overlap(self):
        second = dict(self.config["folders"][0], id="pictures", remote_name="Pictures",
                      local=str(self.root / "Pictures"))
        Path(second["local"]).mkdir()
        self.config["folders"].append(second)
        daemon = self.daemon()
        daemon.config["folders"][1]["initialized"] = True
        config.save(self.paths, daemon.config)
        self.settings(bisync={"sleep": 0.1})
        self.assertTrue(daemon.sync_now())
        daemon._pump()
        self.assertEqual(daemon.queue.current, "docs")
        daemon._pump()
        self.assertEqual(daemon.queue.current, "docs")
        drain_until(lambda: daemon.child is None)
        daemon._pump()
        drain_until(lambda: daemon.child is None)
        events = sorted(self.calls("start") + self.calls("finish"), key=lambda x: x["time"])
        active = set()
        for event in events:
            if event["phase"] == "start":
                self.assertFalse(active)
                active.add(event["pid"])
            else:
                active.remove(event["pid"])
        self.assertEqual([e["argv"][0] for e in events if e["phase"] == "start"],
                         ["lsf", "bisync", "lsf", "bisync"])

    def test_filter_reload_is_deferred_until_active_child_finishes(self):
        daemon = self.daemon()
        self.settings(bisync={"sleep": 0.2})
        daemon.sync_now("docs")
        daemon._pump()
        old_filters = self.paths.filters_for("docs").read_text()
        self.config["excludes"].append("*.pending")
        config.save(self.paths, self.config)
        self.assertTrue(daemon.reload_config())
        self.assertTrue(daemon.pending_reload)
        self.assertEqual(self.paths.filters_for("docs").read_text(), old_filters)
        drain_until(lambda: daemon.child is None)
        daemon._tick()
        self.assertIn("- *.pending", self.paths.filters_for("docs").read_text())
        self.assertTrue(daemon.store.read()["folders"]["docs"]["needs_resync"])

    def test_removed_queued_folder_is_discarded(self):
        daemon = self.daemon()
        daemon.sync_now("docs")
        self.config["folders"] = []
        config.save(self.paths, self.config)
        daemon._pump()
        self.assertEqual(self.calls(), [])
        self.assertIsNone(daemon.lock)

    def test_transient_bisync_error_is_eligible_for_next_cycle(self):
        daemon = self.daemon()
        self.settings(bisync={"code": 5, "log": "ERROR : transient error\n"})
        daemon.sync_now("docs")
        daemon._pump()
        drain_until(lambda: daemon.child is None)
        self.assertTrue(daemon.sync_now("docs"))

    def test_probe_network_failure_skips_sync_without_immediate_notification(self):
        daemon = self.daemon()
        daemon.store.folder("docs", status="ok", last_success="previous success")
        messages = []
        daemon.notifier = Notifier(send=lambda *message: messages.append(message))
        self.settings(lsf={"code": 5, "output": "ERROR dial tcp: network is unreachable"})
        daemon.sync_now("docs")
        daemon._pump()
        drain_until(lambda: daemon.child is None)
        self.assertEqual([c["argv"][0] for c in self.calls()], ["lsf"])
        self.assertEqual(daemon.store.read()["daemon"]["status"], "offline")
        self.assertEqual(daemon.store.read()["daemon"]["last_error"], i18n.PROBE_ERROR)
        item = daemon.store.read()["folders"]["docs"]
        self.assertEqual(item["status"], "ok")
        self.assertIsNone(item["last_error"])
        self.assertEqual(item["last_success"], "previous success")
        self.assertFalse(item["needs_resync"])
        self.assertEqual(messages, [])
        daemon.next_poll = time.monotonic() + 300
        daemon.offline_since -= 1801
        daemon._tick()
        daemon._tick()
        self.assertEqual(len(messages), 1)

    def test_offline_restores_prior_error_and_pending_folder_statuses(self):
        daemon = self.daemon()
        second = dict(daemon.config["folders"][0], id="pictures", remote_name="Pictures",
                      local=str(self.root / "Pictures"))
        Path(second["local"]).mkdir()
        daemon.config["folders"].append(second)
        config.save(self.paths, daemon.config)
        daemon.reload_config()
        daemon.store.folder("docs", status="error", last_error="previous folder failure")
        daemon.store.folder("pictures", status="ok")
        self.settings(lsf={"code": 5, "output": "ERROR dial tcp: network is unreachable"})
        daemon.sync_now()
        daemon.sync_now()  # Coalescing must not replace the saved status with queued.
        daemon._pump()
        drain_until(lambda: daemon.child is None)
        value = daemon.store.read()
        self.assertEqual(value["folders"]["docs"]["status"], "error")
        self.assertEqual(value["folders"]["docs"]["last_error"], "previous folder failure")
        self.assertEqual(value["folders"]["pictures"]["status"], "ok")
        self.assertIsNone(value["folders"]["pictures"]["last_error"])
        self.assertEqual(value["daemon"]["status"], "offline")
        self.assertEqual(value["daemon"]["last_error"], i18n.PROBE_ERROR)
        self.assertFalse(daemon.queue.pending)
        self.assertFalse(daemon.previous_statuses)

    def test_successful_probe_clears_daemon_offline_error(self):
        daemon = self.daemon()
        self.settings(lsf={"code": 5, "output": "ERROR dial tcp: network is unreachable"})
        daemon.sync_now("docs")
        daemon._pump()
        drain_until(lambda: daemon.child is None)
        daemon.reload_config()
        self.assertEqual(daemon.store.read()["daemon"]["last_error"], i18n.PROBE_ERROR)
        self.settings()
        daemon.sync_now("docs")
        daemon._pump()
        drain_until(lambda: daemon.child is None)
        value = daemon.store.read()
        self.assertIsNone(daemon.offline_since)
        self.assertEqual(value["daemon"]["status"], "idle")
        self.assertIsNone(value["daemon"]["last_error"])
        self.assertEqual(value["folders"]["docs"]["status"], "ok")

    def test_probe_auth_failure_is_an_error_not_offline(self):
        daemon = self.daemon()
        self.settings(lsf={"code": 5, "output": "ERROR invalid_grant"})
        daemon.sync_now("docs")
        daemon._pump()
        drain_until(lambda: daemon.child is None)
        self.assertIsNone(daemon.offline_since)
        self.assertEqual(daemon.store.read()["daemon"]["status"], "error")
        self.assertEqual(daemon.store.read()["folders"]["docs"]["status"], "error")
        self.assertEqual(daemon.store.read()["folders"]["docs"]["last_error"], i18n.PROBE_ERROR)

    def test_exit7_and_safety_abort_gate_subsequent_runs(self):
        for text, status in (("ERROR fatal failure\n", "error"),
                             ("ERROR Safety abort: too many deletes\n", "blocked_delete")):
            with self.subTest(status=status):
                daemon = self.daemon()
                daemon.store.folder("docs", status="ok", needs_resync=False)
                self.settings(bisync={"code": 7, "log": text})
                daemon.sync_now("docs")
                daemon._pump()
                drain_until(lambda: daemon.child is None)
                item = daemon.store.read()["folders"]["docs"]
                self.assertEqual(item["status"], status)
                self.assertFalse(daemon.sync_now("docs"))

    def test_pause_persists_and_clears_pending_work(self):
        daemon = self.daemon()
        daemon.sync_now("docs")
        self.assertTrue(daemon.pause(True))
        self.assertTrue(config.load(self.paths)["paused"])
        self.assertFalse(daemon.queue.pending)
        self.assertFalse(daemon.sync_now("docs"))
        self.assertTrue(daemon.pause(False))
        daemon.reload_config()
        self.assertFalse(config.load(self.paths)["paused"])
        self.assertTrue(daemon.sync_now("docs"))

    def test_daemon_waits_for_cli_lock(self):
        daemon = self.daemon()
        daemon.sync_now("docs")
        with execution_lock(self.paths, "docs"):
            daemon._pump()
            self.assertIsNone(daemon.child)
            self.assertIn("docs", daemon.queue.pending)
        daemon._pump()
        drain_until(lambda: daemon.child is None)

    def test_startup_and_reload_preserve_external_syncing_status(self):
        for initialized in (False, True):
            for engine in (False, True):
                with self.subTest(initialized=initialized, engine=engine):
                    self.config["folders"][0]["initialized"] = initialized
                    config.save(self.paths, self.config)
                    self.prepare()
                    self.store.folder("docs", status="syncing", last_error="external run")
                    with self.external_folder_lock(engine=engine):
                        with patch("drivesync.daemon.Notifier", return_value=Notifier(send=lambda *_: None)):
                            daemon = Daemon(self.paths)
                        self.addCleanup(daemon.watchers.close)
                        for _ in range(2):
                            self.assertTrue(daemon.reload_config())
                            self.assertEqual(self.store.read()["folders"]["docs"]["status"], "syncing")
                            self.assertFalse(daemon.enqueue("docs", "poll"))
                            self.assertEqual(self.store.read()["folders"]["docs"]["last_error"], "external run")
                        self.assertEqual(self.calls(), [])

    def test_startup_recovers_unlocked_interrupted_initialized_run(self):
        daemon = self.daemon()
        daemon.reconciled = False
        self.store.folder("docs", status="syncing")
        daemon.reload_config()
        self.assertEqual(self.store.read()["folders"]["docs"]["status"], "error")
        self.assertEqual(self.store.read()["folders"]["docs"]["last_error"], i18n.INTERRUPTED)

    def test_filter_reload_stops_initialized_folder(self):
        daemon = self.daemon()
        self.config["excludes"].append("*.extra")
        config.save(self.paths, self.config)
        self.assertTrue(daemon.reload_config())
        self.assertFalse(daemon.sync_now("docs"))
        self.assertTrue(daemon.store.read()["folders"]["docs"]["needs_resync"])

    def test_bad_reload_retains_last_config_and_stops_queue(self):
        daemon = self.daemon()
        daemon.sync_now("docs")
        self.paths.config.write_text("not json")
        self.assertFalse(daemon.reload_config())
        self.assertFalse(daemon.sync_now("docs"))
        self.assertFalse(daemon.queue.pending)
        self.assertEqual(daemon.store.read()["daemon"]["status"], "error")

    def test_status_payload_contains_state_and_config_summary(self):
        daemon = self.daemon()
        value = json.loads(daemon.status_json())
        self.assertFalse(value["paused"])
        self.assertEqual(value["folder_config"][0]["remote_path"], "test:Linux-PC/Documents")
        self.assertEqual(value["folders"], daemon.store.read()["folders"])

    def test_graceful_shutdown_lets_child_finish(self):
        daemon = self.daemon()
        self.settings(bisync={"sleep": 0.25})
        daemon.sync_now("docs")
        daemon._pump()
        drain_until(lambda: any(c["argv"][0] == "bisync" for c in self.calls()))
        daemon.shutdown()
        self.assertTrue(daemon.stopping)
        self.assertFalse(daemon.enqueue("docs", "manual"))
        drain_until(lambda: daemon.child is None)
        self.assertEqual(daemon.store.read()["folders"]["docs"]["status"], "ok")
        for source in daemon.sources:
            if GLib.MainContext.default().find_source_by_id(source):
                GLib.source_remove(source)

    def test_notifications_are_suppressed_until_recovery(self):
        messages = []
        notify = Notifier(send=lambda *message: messages.append(message))
        item = {"status": "error", "needs_resync": False, "last_error": i18n.SYNC_ERROR}
        notify.folder("docs", "Documents", item)
        notify.folder("docs", "Documents", item)
        self.assertEqual(len(messages), 1)
        notify.folder("docs", "Documents", {"status": "ok", "needs_resync": False, "conflicts": 2})
        self.assertIn("2 dosyada", messages[-1][1])
        notify.folder("docs", "Documents", item)
        self.assertEqual(len(messages), 3)

    def test_local_change_uses_same_queue_and_pause_gate(self):
        daemon = self.daemon()
        daemon.watchers.debounce.seconds = .001
        daemon.watchers.debounce.changed("docs")
        drain_until(lambda: bool(daemon.queue.pending))
        self.assertEqual(daemon.queue.pending["docs"], {"local-change"})
        self.assertEqual(self.calls(), [])
        daemon.pause(True)
        daemon.watchers.debounce.changed("docs")
        drain_until(lambda: not daemon.watchers.debounce.pending)
        self.assertFalse(daemon.queue.pending)

    def test_watcher_is_suppressed_while_running_and_during_grace_period(self):
        daemon = self.daemon()
        daemon.sync_now("docs")
        daemon._pump()
        self.assertIn("docs", daemon.watchers.debounce.running)
        self.assertTrue(daemon.watchers.debounce.ignored("docs"))
        daemon.watchers.debounce.changed("docs")
        self.assertFalse(daemon.watchers.debounce.pending)
        drain_until(lambda: daemon.child is None)
        self.assertNotIn("docs", daemon.watchers.debounce.running)
        self.assertTrue(daemon.watchers.debounce.ignored("docs"))
        self.assertFalse(daemon.queue.pending)

    def test_newly_initialized_and_removed_folder_updates_watches(self):
        daemon = self.daemon(initialized=False)
        self.assertEqual(daemon.watchers.count, 0)
        daemon.config["folders"][0]["initialized"] = True
        config.save(self.paths, daemon.config)
        daemon.reload_config()
        self.assertEqual(daemon.watchers.count, 1)
        config.save(self.paths, dict(daemon.config, folders=[]))
        daemon.reload_config()
        self.assertEqual(daemon.watchers.count, 0)

    def test_monitor_cap_fallback_remains_eligible_for_polling(self):
        daemon = self.daemon()
        (Path(daemon.config["folders"][0]["local"]) / "nested").mkdir()
        config.save(self.paths, dict(daemon.config, max_monitors=1))
        daemon.reload_config()
        self.assertIn("docs", daemon.watchers.fallback)
        daemon.enqueue("docs", "poll")
        self.assertEqual(daemon.queue.pending["docs"], {"poll"})
