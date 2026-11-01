import gc
import json
import importlib.util
import os
import sys
from types import ModuleType, SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

from support import IsolatedTest, ROOT
from drivesync import config, nautilus_model as model

import gi
import gi.repository


class MenuItem:
    def __init__(self, **props):
        self.props = SimpleNamespace(**props)
        self.handlers = {}

    def connect(self, signal, callback):
        self.handlers[signal] = callback

    def emit(self, signal):
        self.handlers[signal](self)


# Load the real shell with a fake Nautilus typelib: no Nautilus installation,
# process, or main loop is needed. Gio/GLib/GObject still use real GI.
nautilus = ModuleType("gi.repository.Nautilus")
nautilus.InfoProvider = type("InfoProvider", (), {})
nautilus.MenuProvider = type("MenuProvider", (), {})
nautilus.MenuItem = MenuItem
spec = importlib.util.spec_from_file_location("drivesync._test_nautilus_ext", ROOT / "src/drivesync/nautilus_ext.py")
ext = importlib.util.module_from_spec(spec)
with patch.object(gi, "require_version"), patch.dict(sys.modules, {"gi.repository.Nautilus": nautilus}):
    spec.loader.exec_module(ext)


class File:
    def __init__(self, path):
        self.path = Path(path)
        self.emblems = []
        self.invalidations = 0

    def get_uri_scheme(self):
        return "file"

    def get_location(self):
        return self

    def get_uri(self):
        return self.path.as_uri()

    def get_path(self):
        return str(self.path)

    def get_name(self):
        return self.path.name

    def is_directory(self):
        return False

    def is_gone(self):
        return False

    def add_emblem(self, name):
        self.emblems.append(name)

    def invalidate_extension_info(self):
        self.invalidations += 1


class NautilusExtensionTests(IsolatedTest):
    def setUp(self):
        super().setUp()
        with patch.object(ext.DriveSyncExtension, "_tick"), patch.object(ext.GLib, "timeout_add", return_value=0):
            self.extension = ext.DriveSyncExtension()
        self.config["folders"][0]["initialized"] = True
        self.state = {"folders": {"docs": {"status": "ok", "last_success": "2026-11-01T12:00:00+00:00"}}}
        config.save(self.paths, self.config)
        self.paths.state.write_text(json.dumps(self.state))
        self.clock = [0.0]
        self.extension.cache = model.SnapshotCache(self.paths, lambda: self.clock[0])
        self.file = File(Path(self.config["folders"][0]["local"]) / "rapor #1.txt")
        self.file.path.write_text("local")
        self.success = 1793534400.0
        os.utime(self.file.path, (self.success, self.success))

    def signal(self, state):
        parameters = ext.GLib.Variant("(s)", (json.dumps(state),))
        self.extension._status_changed(None, None, None, None, None, parameters)

    def test_first_callback_reads_snapshot_and_adds_ok_immediately(self):
        self.assertEqual(self.extension.config, {})
        self.assertEqual(self.extension.state, {})
        with patch.object(ext.os, "stat", wraps=os.stat) as stat:
            self.extension.update_file_info(self.file)
        self.assertEqual(self.file.emblems, [model.OK])
        file_stats = [call for call in stat.call_args_list if call.args[0] == self.file.get_path()]
        self.assertEqual(len(file_stats), 1)
        self.assertEqual(file_stats[0].kwargs, {"follow_symlinks": False})
        self.assertFalse(self.calls())

    def test_first_callback_newer_than_success_adds_sync(self):
        os.utime(self.file.path, (self.success + 1, self.success + 1))
        self.extension.update_file_info(self.file)
        self.assertEqual(self.file.emblems, [model.SYNC])

    def test_each_callback_uses_current_file_mtime(self):
        self.extension.update_file_info(self.file)
        os.utime(self.file.path, (self.success + 1, self.success + 1))
        self.extension.update_file_info(self.file)
        self.assertEqual(self.file.emblems, [model.OK, model.SYNC])

    def test_errors_never_raise_or_add_success_emblem(self):
        bad = Mock()
        bad.get_uri_scheme.side_effect = RuntimeError("bad Nautilus object")
        self.extension.update_file_info(bad)
        self.assertEqual(self.extension.get_file_items([bad]), [])
        with patch.object(ext.os, "stat", side_effect=OSError("stat failed")):
            self.extension.update_file_info(self.file)
        self.assertEqual(self.file.emblems, [])
        with patch.object(self.extension.cache, "read", side_effect=RuntimeError("read failed")):
            self.extension.update_file_info(self.file)
        self.assertEqual(self.file.emblems, [])
        self.paths.config.write_text('{"folders": "malformed"}')
        self.clock[0] += 1
        self.extension.update_file_info(self.file)
        self.assertEqual(self.file.emblems, [])
        self.assertEqual(self.extension.get_file_items([self.file]), [])
        self.assertFalse(self.calls())

    def test_symlink_is_not_badged(self):
        link = File(self.file.path.with_name("link.txt"))
        link.path.symlink_to(self.file.path)
        self.extension.update_file_info(link)
        self.assertEqual(link.emblems, [])

    def test_signal_invalidates_affected_folder_and_new_emblem(self):
        other = File(self.root / "outside")
        self.extension.update_file_info(self.file)
        self.extension.update_file_info(other)
        self.file.invalidations = 0
        self.signal({"folders": {"docs": {"status": "syncing"}}})
        self.assertEqual(self.file.invalidations, 1)
        self.assertEqual(other.invalidations, 0)
        self.extension.update_file_info(self.file)
        self.assertEqual(self.file.emblems[-1], model.SYNC)
        self.signal({"folders": {"docs": {"status": "syncing"}}})
        self.assertEqual(self.file.invalidations, 1)
        self.extension._status_changed(None, None, None, None, None, ext.GLib.Variant("(s)", ("broken",)))

    def test_signal_wins_over_unchanged_cached_snapshot(self):
        self.extension.update_file_info(self.file)
        self.signal({"folders": {"docs": {"status": "syncing"}}})
        self.extension.update_file_info(self.file)
        self.assertEqual(self.file.emblems[-1], model.SYNC)
        self.clock[0] += 1
        self.extension.update_file_info(self.file)
        self.assertEqual(self.file.emblems[-1], model.SYNC)
        self.paths.state.write_text(json.dumps({"folders": {"docs": {"status": "error"}}}))
        self.clock[0] += 1
        self.extension.update_file_info(self.file)
        self.assertEqual(self.file.emblems[-1], model.ERROR)

    def test_registry_is_weak_and_bounded(self):
        files = [File(Path(self.file.get_path() + str(n))) for n in range(ext.MAX_FILES + 10)]
        for file in files:
            self.extension._remember(file)
        self.assertEqual(len(self.extension.files), ext.MAX_FILES)
        del file
        files.clear()
        gc.collect()
        self.extension._invalidate()
        self.assertEqual(len(self.extension.files), 0)

    def test_menu_calls_only_folder_sync_and_encoded_search(self):
        self.extension.bus = Mock()
        with patch.object(ext.Gio.AppInfo, "launch_default_for_uri_async") as launch:
            sync, drive = self.extension.get_file_items([self.file])
            self.assertEqual(sync.props.label, "DriveSync: Şimdi eşitle")
            self.assertEqual(drive.props.label, "DriveSync: Drive'da göster")
            sync.emit("activate")
            args = self.extension.bus.call.call_args.args
            self.assertEqual(args[3], "SyncNow")
            self.assertEqual(args[4].unpack(), ("docs",))
            drive.emit("activate")
            self.assertEqual(launch.call_args.args[0], model.drive_search_url(self.file.get_name()))
        excluded = File(Path(self.config["folders"][0]["local"]) / "a.tmp")
        self.assertEqual(self.extension.get_file_items([excluded]), [])
        self.assertEqual(self.extension.get_file_items([File(self.root / "other")]), [])
        self.assertEqual(len(self.extension.get_background_items(self.file)), 2)
        self.extension.bus = None
        self.extension._sync({"docs"})

    def tick(self):
        self.clock[0] += 1
        self.extension.bus_ready = True
        self.extension._tick()

    def test_timer_invalidates_only_affected_folders_without_file_stats(self):
        other_folder = dict(self.config["folders"][0], id="other", remote_name="Other", local=str(self.root / "other"))
        self.config["folders"].append(other_folder)
        config.save(self.paths, self.config)
        other = File(self.root / "other" / "file.txt")
        self.extension._refresh_snapshot()
        self.extension._remember(self.file)
        self.extension._remember(other)
        self.paths.state.write_text(json.dumps({"folders": {"docs": {"status": "error"}}}))
        with patch.object(ext.os, "stat", wraps=os.stat) as stat:
            self.tick()
        self.assertEqual(self.file.invalidations, 1)
        self.assertEqual(other.invalidations, 0)
        self.assertFalse(any(call.args[0] in (self.file.get_path(), other.get_path())
                             for call in stat.call_args_list))
        self.tick()
        self.assertEqual(self.file.invalidations, 1)
        self.config["folders"] = [other_folder]
        config.save(self.paths, self.config)
        self.tick()
        self.assertEqual(self.file.invalidations, 2)
        self.assertEqual(other.invalidations, 0)

    def test_timer_detects_excludes_and_snapshot_errors(self):
        self.extension.update_file_info(self.file)
        self.file.invalidations = 0
        self.config["excludes"].append("*.txt")
        config.save(self.paths, self.config)
        self.tick()
        self.assertEqual(self.file.invalidations, 1)
        self.paths.config.write_text("broken")
        self.tick()
        self.assertEqual(self.file.invalidations, 2)
        self.extension.update_file_info(self.file)
        self.assertEqual(self.file.emblems, [model.OK])

    def test_tick_refreshes_even_if_bus_connection_raises_and_retries(self):
        with patch.object(ext.Gio, "bus_get", side_effect=RuntimeError("no bus")) as bus:
            self.extension._tick()
            self.assertEqual(self.extension.config, self.config)
            self.assertFalse(self.extension.bus_pending)
            self.extension._tick()
            self.assertEqual(bus.call_count, 1)
            self.extension.bus_retry = 0
            self.extension._tick()
            self.assertEqual(bus.call_count, 2)
