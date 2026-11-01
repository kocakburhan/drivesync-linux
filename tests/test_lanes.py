"""Two execution lanes: long init/resync jobs must not starve routine syncs of other folders."""

from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import patch

from gi.repository import GLib

from support import IsolatedTest
from drivesync import config, filters, i18n
from drivesync.daemon import Daemon
from drivesync.notify import Notifier
from drivesync.state import execution_lock
from test_daemon import drain_until
from test_power import FakePower


class LaneTests(IsolatedTest):
    """Folders: docs (A), pics (B, initialized), more (C, uninitialized)."""

    def build(self, **overrides):
        for folder_id, name, initialized in (("pics", "Pictures", True), ("more", "More", False)):
            path = self.root / name
            path.mkdir()
            self.config["folders"].append(dict(self.config["folders"][0], id=folder_id, remote_name=name,
                                               local=str(path), initialized=initialized))
        self.config.update(overrides)
        config.save(self.paths, self.config)
        with patch("drivesync.daemon.Notifier", return_value=Notifier(send=lambda *_: None)):
            daemon = Daemon(self.paths, self.power) if hasattr(self, "power") else Daemon(self.paths)
        daemon.next_poll = float("inf")
        daemon.reload_config()
        # B counts as an already synced folder (its first filter baseline was just recorded).
        self.store.folder("pics", status="ok", needs_resync=False, last_error=None)
        self.addCleanup(daemon.watchers.close)
        self.addCleanup(self.kill_all, daemon)
        return daemon

    def kill_all(self, daemon):
        for lane in daemon.lanes.values():
            if lane.child:
                lane.child.kill()
        try:
            drain_until(lambda: not any(lane.child for lane in daemon.lanes.values()), timeout=5)
        except AssertionError:
            pass

    def approve(self, daemon, folder_id):
        folder = config.folder_by_id(daemon.config, folder_id)
        digest = filters.digest_of(config.effective_excludes(daemon.config, folder))
        pair = config.pair_hash(daemon.config, folder)
        request = dict(kind="init", approved_at=datetime.now(timezone.utc).isoformat(),
                       approved_plan={"to_remote": 1, "to_local": 1, "deletes": 1},
                       filters_sha256=digest, pair_sha256=pair)
        self.store.folder(folder_id, init_request=request, status="init_queued",
                          dry_run_ok_at=datetime.now(timezone.utc).isoformat(),
                          dry_run_filters_sha256=digest, dry_run_pair_sha256=pair)

    def item(self, folder_id):
        return self.store.read()["folders"][folder_id]

    def resyncs(self, phase="start"):
        return [c for c in self.calls(phase) if "--resync" in c["argv"]]

    def routine(self, phase="start"):
        return [c for c in self.calls(phase) if c["argv"][0] == "bisync" and "--resync" not in c["argv"]]

    def start_init(self, daemon, folder_id="docs"):
        self.approve(daemon, folder_id)
        self.assertTrue(daemon.start_init(folder_id, "init"))
        daemon._pump()

    def test_routine_sync_runs_and_finishes_during_a_long_init(self):
        self.settings(resync={"sleep": 2.5}, bisync={"sleep": 0.1})
        daemon = self.build()
        self.start_init(daemon)
        drain_until(lambda: self.resyncs())
        long_lane, normal_lane = daemon.lanes["long"], daemon.lanes["normal"]
        self.assertEqual(long_lane.folder_id, "docs")
        # inotify (or polling) reports a change in the initialized folder B.
        self.assertTrue(daemon.enqueue("pics", "local-change"))
        daemon._pump()
        self.assertEqual(normal_lane.folder_id, "pics")
        status = daemon.store.read()["daemon"]
        self.assertEqual((status["status"], status["current_folder"], status["current_folders"]),
                         ("syncing", "docs", ["docs", "pics"]))
        drain_until(lambda: not normal_lane.busy)
        self.assertEqual(self.item("pics")["status"], "ok")
        self.assertTrue(long_lane.busy)            # B finished while A is still uploading
        self.assertEqual(self.item("docs")["status"], "syncing")
        self.assertEqual(daemon.store.read()["daemon"]["current_folders"], ["docs"])
        self.assertEqual(daemon.store.read()["daemon"]["status"], "syncing")
        drain_until(lambda: not long_lane.busy, timeout=10)
        finishes = {c["argv"][1]: c["time"] for c in self.calls("finish") if c["argv"][0] == "bisync"}
        pics = next(c for c in self.calls("finish") if c["argv"][0] == "bisync" and "--resync" not in c["argv"])
        init = self.resyncs("finish")[0]
        self.assertLess(pics["time"], init["time"])
        self.assertTrue(config.load(self.paths)["folders"][0]["initialized"])
        self.assertEqual(daemon.store.read()["daemon"]["current_folders"], [])
        self.assertIsNone(daemon.store.read()["daemon"]["current_folder"])

    def test_pending_init_folder_never_enters_normal_lane_and_no_double_job(self):
        self.settings(resync={"sleep": 2}, bisync={"sleep": 0.1})
        daemon = self.build()
        self.start_init(daemon)
        drain_until(lambda: self.resyncs())
        for reason in ("local-change", "poll", "manual", "remote-change"):
            self.assertFalse(daemon.enqueue("docs", reason))
        self.assertFalse(daemon.sync_now("docs"))
        self.assertNotIn("docs", daemon.queue.pending)
        # An approved init for B (running a routine sync) cannot start a second job on B.
        daemon.enqueue("pics", "poll")
        daemon._pump()
        self.assertEqual(daemon.lanes["normal"].folder_id, "pics")
        self.approve(daemon, "pics")
        self.assertFalse(daemon._enqueue_init("pics"))
        daemon._pump()
        self.assertEqual({lane.folder_id for lane in daemon.lanes.values()}, {"docs", "pics"})
        self.assertEqual(len(daemon.queue.active), 2)
        # A queued init is long-lane work even if a routine reason is merged into it.
        self.assertEqual(daemon._lane_for({"init", "poll"}), "long")
        self.assertEqual(daemon._lane_for({"poll"}), "normal")

    def test_second_init_waits_for_the_first(self):
        self.settings(resync={"sleep": 0.8})
        daemon = self.build()
        self.approve(daemon, "docs")
        self.approve(daemon, "more")
        daemon._scan_inits()
        daemon._pump()
        self.assertEqual(daemon.lanes["long"].folder_id, "docs")
        self.assertFalse(daemon.lanes["normal"].busy)
        self.assertIn("more", daemon.queue.pending)
        daemon._pump()
        self.assertFalse(daemon.lanes["normal"].busy)
        drain_until(lambda: not daemon.lanes["long"].busy, timeout=10)
        self.assertEqual(self.item("docs")["status"], "ok")
        daemon._pump()
        self.assertEqual(daemon.lanes["long"].folder_id, "more")
        drain_until(lambda: not daemon.lanes["long"].busy, timeout=10)
        self.assertEqual(self.item("more")["status"], "ok")
        starts = sorted(self.resyncs("start") + self.resyncs("finish"), key=lambda c: c["time"])
        self.assertEqual([c["phase"] for c in starts], ["start", "finish", "start", "finish"])

    def test_manual_pause_blocks_both_lanes(self):
        daemon = self.build()
        self.approve(daemon, "docs")
        daemon._scan_inits()
        daemon.enqueue("pics", "poll")
        self.assertEqual(sorted(daemon.queue.pending), ["docs", "pics"])
        daemon.config["paused"] = True
        daemon._pump()
        self.assertEqual(self.calls(), [])
        self.assertFalse(any(lane.busy for lane in daemon.lanes.values()))
        self.assertTrue(daemon.pause(True))
        daemon._tick()
        self.assertFalse(daemon.start_init("docs", "init") and daemon.queue.pending)
        daemon._pump()
        self.assertEqual(self.calls(), [])

    def test_auto_pause_blocks_new_jobs_in_both_lanes_then_resumes_both(self):
        self.power = FakePower(metered=True)
        self.settings(resync={"sleep": 0.3}, bisync={"sleep": 0.3})
        daemon = self.build()
        self.approve(daemon, "docs")
        daemon._scan_inits()
        daemon.enqueue("pics", "poll")
        daemon._pump()
        self.assertEqual(self.calls(), [])
        self.assertFalse(any(lane.busy for lane in daemon.lanes.values()))
        self.power.set(metered=False)
        daemon._pump()
        self.assertEqual({lane.name for lane in daemon.lanes.values() if lane.busy}, {"long", "normal"})
        drain_until(lambda: not any(lane.busy for lane in daemon.lanes.values()), timeout=10)
        self.assertEqual((self.item("docs")["status"], self.item("pics")["status"]), ("ok", "ok"))

    def test_pause_stops_running_terminates_both_lanes_and_requeues(self):
        self.power = FakePower()
        self.settings(resync={"sleep": 5}, bisync={"sleep": 5})
        daemon = self.build(pause_stops_running=True)
        self.start_init(daemon)
        drain_until(lambda: self.resyncs())
        daemon.enqueue("pics", "poll")
        daemon._pump()
        drain_until(lambda: self.routine())
        self.power.set(metered=True)
        self.assertTrue(all(lane.auto_stop for lane in daemon.lanes.values()))
        drain_until(lambda: not any(lane.busy for lane in daemon.lanes.values()), timeout=10)
        self.assertEqual(self.item("docs")["status"], "init_queued")
        self.assertIsNotNone(self.item("docs")["init_request"])
        self.assertIn("pics", daemon.queue.pending)
        self.assertFalse(daemon.auto_stop)

    def test_shutdown_terminates_both_children(self):
        self.settings(resync={"sleep": 20}, bisync={"sleep": 20})
        daemon = self.build()
        self.start_init(daemon)
        drain_until(lambda: self.resyncs())
        daemon.enqueue("pics", "poll")
        daemon._pump()
        drain_until(lambda: self.routine())
        daemon.shutdown()
        self.assertTrue(daemon.stopping)
        self.assertTrue(all(lane.child for lane in daemon.lanes.values()))
        daemon._terminate()  # what the 60 second timer does
        drain_until(lambda: not any(lane.busy for lane in daemon.lanes.values()), timeout=10)
        self.assertEqual(self.item("docs")["status"], "init_queued")   # resumes on next start
        self.assertIsNotNone(self.item("docs")["init_request"])
        self.assertEqual(self.item("pics")["status"], "error")
        self.assertEqual(self.store.read()["daemon"]["current_folders"], [])

    def test_parallel_lanes_false_serializes_everything(self):
        self.settings(resync={"sleep": 1}, bisync={"sleep": 0.1})
        daemon = self.build(parallel_lanes=False)
        self.start_init(daemon)
        drain_until(lambda: self.resyncs())
        daemon.enqueue("pics", "local-change")
        daemon._pump()
        self.assertFalse(daemon.lanes["normal"].busy)
        self.assertEqual(daemon.lanes["long"].folder_id, "docs")
        self.assertEqual(self.routine(), [])
        self.assertEqual(self.item("pics")["status"], "queued")
        # Single-lane mode holds both lane locks: a direct run in either lane is refused.
        for lane in ("long", "normal"):
            with self.assertRaises(Exception):
                with execution_lock(self.paths, lane=lane):
                    pass
        drain_until(lambda: not daemon.lanes["long"].busy, timeout=10)
        daemon._pump()
        self.assertEqual(daemon.lanes["long"].folder_id, "pics")
        drain_until(lambda: not daemon.lanes["long"].busy, timeout=10)
        self.assertEqual(self.item("pics")["status"], "ok")
        events = sorted(self.calls("start") + self.calls("finish"), key=lambda c: c["time"])
        active = set()
        for event in events:
            if event["phase"] == "start":
                self.assertFalse(active)
                active.add(event["pid"])
            else:
                active.discard(event["pid"])

    def test_reload_leaves_running_folders_filters_alone_until_job_ends(self):
        self.settings(resync={"sleep": 1.5})
        daemon = self.build()
        self.start_init(daemon)
        drain_until(lambda: self.resyncs())
        old_docs = self.paths.filters_for("docs").read_text()
        data = config.load(self.paths)
        data["excludes"].append("*.lanes")
        config.save(self.paths, data)
        self.assertTrue(daemon.reload_config())
        self.assertEqual(self.paths.filters_for("docs").read_text(), old_docs)     # in use
        self.assertIn("- *.lanes", self.paths.filters_for("pics").read_text())     # idle folder updated
        self.assertTrue(daemon.refilter)
        # New jobs still start while the long lane is busy (no global reload deferral).
        daemon.enqueue("pics", "poll")
        self.assertFalse(daemon.pending_reload)
        daemon._pump()
        self.assertTrue(daemon.lanes["normal"].busy or self.item("pics")["status"] in ("error", "ok"))
        drain_until(lambda: not any(lane.busy for lane in daemon.lanes.values()), timeout=10)
        self.assertIn("- *.lanes", self.paths.filters_for("docs").read_text())
        self.assertFalse(daemon.refilter)

    def test_status_json_lists_current_folders(self):
        self.settings(resync={"sleep": 1}, bisync={"sleep": 1})
        daemon = self.build()
        self.start_init(daemon)
        daemon.enqueue("pics", "poll")
        daemon._pump()
        value = json.loads(daemon.status_json())
        self.assertEqual(value["daemon"]["current_folders"], ["docs", "pics"])
        self.assertEqual(value["daemon"]["current_folder"], "docs")
        drain_until(lambda: not any(lane.busy for lane in daemon.lanes.values()), timeout=10)


class CliLaneTests(IsolatedTest):
    def test_cli_lane_locks_and_folder_lock(self):
        self.prepare(initialized=True)
        self.store.folder("docs", status="blocked_delete")
        # A held long lane refuses init/resync (also previews) but not force-sync.
        with execution_lock(self.paths, lane="long"):
            for command in (("init", "docs", "--dry-run"), ("resync", "docs", "--dry-run")):
                code, _, error = self.cli(*command)
                self.assertEqual(code, 1)
                self.assertIn(i18n.BUSY, error)
            self.assertNotIn(i18n.BUSY, self.cli("force-sync", "docs", "--yes")[2])
        # A held normal lane refuses force-sync only.
        with execution_lock(self.paths, lane="normal"):
            code, _, error = self.cli("force-sync", "docs", "--yes")
            self.assertEqual(code, 1)
            self.assertIn(i18n.BUSY, error)
            self.assertNotIn(i18n.BUSY, self.cli("resync", "docs", "--dry-run")[2])
        # The folder lock (what a daemon job on that folder holds) refuses everything.
        from drivesync.state import folder_lock
        with folder_lock(self.paths, "docs"):
            for command in (("resync", "docs", "--dry-run"), ("force-sync", "docs", "--yes"),
                            ("resync", "docs", "--yes", "--foreground")):
                code, _, error = self.cli(*command)
                self.assertEqual(code, 1)
                self.assertIn(i18n.BUSY, error)

    def test_parallel_lanes_off_makes_cli_take_both_lanes(self):
        self.prepare(initialized=True)
        data = config.load(self.paths)
        data["parallel_lanes"] = False
        config.save(self.paths, data)
        with execution_lock(self.paths, lane="normal"):
            code, _, error = self.cli("resync", "docs", "--dry-run")
            self.assertEqual(code, 1)
            self.assertIn(i18n.BUSY, error)

    def test_parallel_lanes_config_key(self):
        code, out, _ = self.cli("config", "set", "parallel_lanes", "false", "--json")
        self.assertEqual(code, 0)
        self.assertIs(json.loads(out)["value"], False)
        self.assertEqual(self.cli("config", "set", "parallel_lanes", "3", "--json")[0], 1)
        self.assertIs(config.load(self.paths)["parallel_lanes"], False)
        self.assertIs(config.defaults()["parallel_lanes"], True)
