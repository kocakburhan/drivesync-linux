from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import patch

from support import IsolatedTest
from drivesync import config, remote_watch as rw
from drivesync.daemon import Daemon
from drivesync.notify import Notifier
from test_daemon import drain_until

NOW = datetime.now(timezone.utc)


def iso(delta):
    return (NOW + delta).isoformat()


def drive_time(delta):
    return (NOW + delta).strftime("%Y-%m-%dT%H:%M:%S.123Z")


DIRS = json.dumps([{"Path": "Documents", "ID": "d1", "IsDir": True},
                   {"Path": "Documents/sub", "ID": "d2", "IsDir": True},
                   {"Path": "Pictures", "ID": "p1", "IsDir": True},
                   {"Path": "Other", "ID": "o1", "IsDir": True}])


class PureTests(IsolatedTest):
    def test_command_and_time_format(self):
        since = datetime(2026, 5, 6, 7, 8, 9, 999, tzinfo=timezone(timedelta(hours=3)))
        self.assertEqual(rw.format_time(since), "2026-05-06T04:08:09Z")
        command = rw.query_command(self.config, since)
        self.assertEqual(command[1:4], ["backend", "query", "test:"])
        self.assertEqual(command[4], "(modifiedTime > '2026-05-06T04:08:09Z' or createdTime > "
                                     "'2026-05-06T04:08:09Z') and trashed = false")
        self.assertEqual(rw.list_command(self.config)[1:3], ["lsjson", "test:Linux-PC"])
        self.assertIn("--dirs-only", rw.list_command(self.config))

    def test_parse_query(self):
        self.assertEqual(rw.parse_query(""), [])
        self.assertEqual(rw.parse_query("null"), [])
        self.assertEqual(rw.parse_query('[{"id": "a", "parents": ["p"]}]')[0]["parents"], ["p"])
        for bad in ("{", "{}", "[1]"):
            with self.assertRaises(rw.RemoteCheckError):
                rw.parse_query(bad)

    def test_parse_dirs_and_index_cache(self):
        self.assertEqual(rw.parse_dirs(DIRS, "Documents"), ["d1", "d2"])
        self.assertIsNone(rw.load_index(self.paths, "docs"))
        rw.save_index(self.paths, "docs", ["d2", "d1"])
        self.assertEqual(rw.load_index(self.paths, "docs"), {"d1", "d2"})

    def test_attribution(self):
        since = {"docs": NOW - timedelta(minutes=5), "pics": NOW - timedelta(minutes=5)}
        indexes = {"docs": {"d1", "d2"}, "pics": {"p1"}}
        new, old = drive_time(timedelta()), drive_time(-timedelta(hours=1))
        items = [{"id": "f1", "parents": ["d2"], "modifiedTime": new},
                 {"id": "f2", "parents": ["p1"], "modifiedTime": old, "createdTime": old},
                 {"id": "f3", "parents": ["zzz"], "modifiedTime": new},
                 {"id": "f4", "modifiedTime": new}]
        result = rw.attribute(items, indexes, since)
        self.assertEqual(result.changed, {"docs"})
        self.assertEqual(result.unknown, {"zzz"})
        self.assertEqual(result.loose, {"docs", "pics"})
        self.assertEqual(rw.attribute(items, indexes, since, foreign={"zzz"}).unknown, set())


class DaemonRemoteTests(IsolatedTest):
    def make(self, two=False):
        if two:
            Path(self.root / "Pictures").mkdir()
            self.config["folders"].append({"id": "pics", "local": str(self.root / "Pictures"),
                                           "remote_name": "Pictures", "enabled": True, "initialized": True})
            config.save(self.paths, self.config)
        self.prepare(initialized=True)
        with patch("drivesync.daemon.Notifier", return_value=Notifier(send=lambda *_: None)):
            daemon = Daemon(self.paths)
        daemon.reload_config()
        daemon.next_poll = float("inf")
        self.addCleanup(daemon.watchers.close)
        for folder in self.config["folders"]:
            self.store.folder(folder["id"], status="ok", needs_resync=False, last_error=None, last_full_sync=iso(-timedelta(minutes=10)),
                              last_remote_check=iso(-timedelta(minutes=10)))
        return daemon

    def poll(self, daemon):
        daemon._poll()
        drain_until(lambda: not daemon.checker.running)

    def rc(self, name):
        return [c for c in self.calls() if c["argv"][0] == name]

    def test_no_changes_enqueues_nothing(self):
        daemon = self.make()
        self.settings(lsjson=dict(output=DIRS), query=dict(output="[]"))
        self.poll(daemon)
        self.assertEqual(len(self.rc("backend")), 1)
        self.assertFalse(daemon.queue.pending)
        self.assertEqual(self.rc("bisync"), [])
        checked = rw.parse_time(self.store.read()["folders"]["docs"]["last_remote_check"])
        self.assertGreater(checked, NOW - rw.SKEW - timedelta(minutes=5))

    def test_only_changed_folder_is_enqueued(self):
        daemon = self.make(two=True)
        item = {"id": "f", "name": "x", "parents": ["p1"], "modifiedTime": drive_time(timedelta(seconds=1))}
        self.settings(lsjson=dict(output=DIRS), query=dict(output=json.dumps([item])))
        self.poll(daemon)
        self.assertEqual(list(daemon.queue.pending), ["pics"])
        self.assertEqual(len(self.rc("lsjson")), 1)

    def test_unknown_parent_refreshes_cache_once(self):
        daemon = self.make()
        rw.save_index(self.paths, "docs", ["d1"])
        daemon.checker.stale = False
        dirs = json.loads(DIRS) + [{"Path": "Documents/new", "ID": "d9", "IsDir": True}]
        item = {"id": "f", "parents": ["d9"], "modifiedTime": drive_time(timedelta(seconds=1))}
        self.settings(lsjson=dict(output=json.dumps(dirs)), query=dict(output=json.dumps([item])))
        self.poll(daemon)
        self.assertEqual(len(self.rc("lsjson")), 1)
        self.assertEqual(list(daemon.queue.pending), ["docs"])
        # A parent that stays unknown is outside our tree: ignored, not re-listed next time.
        daemon.queue.clear()
        item["parents"] = ["elsewhere"]
        self.settings(lsjson=dict(output=DIRS), query=dict(output=json.dumps([item])))
        self.poll(daemon)
        self.assertFalse(daemon.queue.pending)
        self.assertEqual(len(self.rc("lsjson")), 2)
        self.poll(daemon)
        self.assertEqual(len(self.rc("lsjson")), 2)

    def test_full_sync_safety_net(self):
        daemon = self.make()
        self.store.folder("docs", last_full_sync=iso(-timedelta(hours=2)))
        self.settings(lsjson=dict(output=DIRS), query=dict(output="[]"))
        self.poll(daemon)
        self.assertEqual(list(daemon.queue.pending), ["docs"])
        self.assertEqual(self.rc("backend"), [])

    def test_failure_falls_back_to_full_bisync(self):
        daemon = self.make()
        self.settings(lsjson=dict(output=DIRS), query=dict(code=1, output="boom"))
        self.poll(daemon)
        self.assertEqual(list(daemon.queue.pending), ["docs"])
        self.assertTrue(daemon.remote_warned)

    def test_config_key(self):
        self.assertEqual(config.defaults()["full_sync_interval_minutes"], 60)
        code, out, _ = self.cli("config", "set", "full_sync_interval_minutes", "30", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["value"], 30)
        for bad in ("0", "-1", "null", "false"):
            self.assertEqual(self.cli("config", "set", "full_sync_interval_minutes", bad)[0], 1)
