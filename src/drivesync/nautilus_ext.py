"""Thin Nautilus 4.0 shell with local stat and throttled JSON snapshots."""

from collections import OrderedDict
import json
import os
import stat
import time
import weakref

import gi

gi.require_version("Nautilus", "4.0")
from gi.repository import Gio, GLib, GObject, Nautilus

from drivesync import dbus_service, i18n, nautilus_model as model
from drivesync.paths import Paths

MAX_FILES = 512


class DriveSyncExtension(GObject.GObject, Nautilus.InfoProvider, Nautilus.MenuProvider):
    def __init__(self):
        super().__init__()
        self.files = OrderedDict()
        self.config, self.state = {}, {}
        self.snapshot_state = None
        self.cache = model.SnapshotCache(Paths.from_env())
        self.bus = self.subscription = None
        self.bus_pending = self.bus_ready = False
        self.bus_retry = 0
        # Nautilus owns the provider for its lifetime. This callback holds only a
        # weak reference; nothing here retains the FileInfo objects themselves.
        ref = weakref.ref(self)
        def tick():
            obj = ref()
            if obj is None:
                return GLib.SOURCE_REMOVE
            obj._tick()
            return GLib.SOURCE_CONTINUE
        self.timer = GLib.timeout_add(1000, tick)
        self._tick()

    def _tick(self):
        try:
            if not self.bus_pending and not self.bus_ready and time.monotonic() >= self.bus_retry:
                self.bus_pending = True
                self.bus_retry = time.monotonic() + 30
                Gio.bus_get(Gio.BusType.SESSION, None, self._bus_connected)
        except Exception:
            self.bus_pending = False
        try:
            self._refresh_snapshot()
        except Exception:
            pass

    def _bus_connected(self, source, result):
        self.bus_pending = False
        try:
            self.bus = Gio.bus_get_finish(result)
            self.bus.set_exit_on_close(False)
            self.subscription = self.bus.signal_subscribe(
                dbus_service.BUS_NAME, dbus_service.INTERFACE, "StatusChanged",
                dbus_service.OBJECT_PATH, None, Gio.DBusSignalFlags.NONE, self._status_changed)
            self.bus.connect("closed", self._bus_closed)
            self.bus_ready = True
        except Exception:
            self.bus = None

    def _bus_closed(self, *_):
        try:
            if self.subscription is not None:
                self.bus.signal_unsubscribe(self.subscription)
        except Exception:
            pass
        self.bus = self.subscription = None
        self.bus_ready = False

    def _status_changed(self, connection, sender, path, interface, signal, parameters):
        try:
            value = json.loads(parameters.unpack()[0])
            old, new = self.state.get("folders", {}), value["folders"]
            affected = {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}
            self.state = value
            self._invalidate(affected)
        except Exception:
            pass

    def _invalidate(self, affected=None, previous_config=None):
        for uri, (ref, path) in list(self.files.items()):
            try:
                file = ref()
                if file is None or file.is_gone():
                    self.files.pop(uri, None)
                    continue
                if affected is None:
                    file.invalidate_extension_info()
                    continue
                folder, _ = model.folder_for_path(path, self.config.get("folders", []))
                previous, _ = model.folder_for_path(path, (previous_config or {}).get("folders", []))
                if any(item and item["id"] in affected for item in (folder, previous)):
                    file.invalidate_extension_info()
            except Exception:
                pass

    def _refresh_snapshot(self):
        # All calls run on the Nautilus main loop. SnapshotCache throttles even
        # the JSON stats and reuses unchanged snapshots, including during ticks.
        try:
            config, state = self.cache.read()
        except Exception:
            config, state = {}, {}
        previous_config, previous_state = self.config, self.state
        self.config = config
        # A signal may be newer than the cached disk snapshot. Keep it until
        # the on-disk state changes, rather than undoing it on the next callback.
        if state != self.snapshot_state:
            self.state = self.snapshot_state = state
        try:
            old = {folder["id"]: folder for folder in previous_config.get("folders", [])}
            new = {folder["id"]: folder for folder in config.get("folders", [])}
            affected = {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}
            if previous_config.get("excludes", []) != config.get("excludes", []):
                affected.update(old.keys() | new.keys())
            old_state, new_state = previous_state.get("folders", {}), self.state.get("folders", {})
            affected.update(key for key in old_state.keys() | new_state.keys()
                            if old_state.get(key) != new_state.get(key))
            if affected:
                self._invalidate(affected, previous_config)
        except Exception:
            # Malformed snapshots must also clear any previously shown badges.
            self._invalidate()

    def _remember(self, file):
        if file.get_uri_scheme() != "file":
            return None
        path = file.get_location().get_path()
        if not path:
            return None
        uri = file.get_uri()
        self.files[uri] = (weakref.ref(file), path)
        self.files.move_to_end(uri)
        while len(self.files) > MAX_FILES:
            self.files.popitem(last=False)
        return path

    def update_file_info(self, file):
        try:
            path = self._remember(file)
            if path:
                self._refresh_snapshot()
                info = os.stat(path, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    return
                emblem = model.emblem_for(path, file.is_directory(), info.st_mtime,
                                          self.config, self.state)
                if emblem:
                    file.add_emblem(emblem)
        except Exception:
            pass

    def get_file_items(self, files):
        try:
            if not files:
                return []
            self._refresh_snapshot()
            folders = set()
            for file in files:
                path = self._remember(file)
                if not path:
                    return []
                folder, relative = model.folder_for_path(path, self.config.get("folders", []))
                if not folder or model.excluded(relative, file.is_directory(), self.config.get("excludes", [])):
                    return []
                folders.add(folder["id"])
            sync = Nautilus.MenuItem(name="DriveSync::sync", label=i18n.NAUTILUS_SYNC)
            sync.connect("activate", lambda *_: self._sync(folders))
            items = [sync]
            if len(files) == 1:
                uri = model.drive_search_url(files[0].get_name())
                drive = Nautilus.MenuItem(name="DriveSync::drive", label=i18n.NAUTILUS_DRIVE)
                drive.connect("activate", lambda *_: self._open(uri))
                items.append(drive)
            return items
        except Exception:
            return []

    def get_background_items(self, folder):
        return self.get_file_items([folder])

    def _sync(self, folders):
        try:
            if self.bus is None:
                return
            def finished(connection, result):
                try:
                    connection.call_finish(result)
                except Exception:
                    pass
            for folder_id in sorted(folders):
                self.bus.call(dbus_service.BUS_NAME, dbus_service.OBJECT_PATH, dbus_service.INTERFACE,
                              "SyncNow", GLib.Variant("(s)", (folder_id,)), None,
                              Gio.DBusCallFlags.NO_AUTO_START, 3000, None, finished)
        except Exception:
            pass

    def _open(self, uri):
        try:
            def finished(source, result):
                try:
                    Gio.AppInfo.launch_default_for_uri_finish(result)
                except Exception:
                    pass
            Gio.AppInfo.launch_default_for_uri_async(uri, None, None, finished)
        except Exception:
            pass
