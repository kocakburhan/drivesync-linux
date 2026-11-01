"""Recursive Gio directory monitors and injectable, trailing-edge debounce."""

import logging
import os
from pathlib import Path
import time

from . import filters, i18n


def gio_monitor(path):
    from gi.repository import Gio
    return Gio.File.new_for_path(str(path)).monitor_directory(
        Gio.FileMonitorFlags.WATCH_MOVES, None)


class Debouncer:
    """Scheduler returns a handle; callbacks run once and return False to GLib."""
    def __init__(self, enqueue, schedule, cancel, clock=time.monotonic, seconds=10):
        self.enqueue, self.schedule, self.cancel, self.clock = enqueue, schedule, cancel, clock
        self.seconds = seconds
        self.pending = {}
        self.running = set()
        self.ignore_until = {}

    def ignored(self, folder_id):
        return folder_id in self.running or self.clock() < self.ignore_until.get(folder_id, 0)

    def discard(self, folder_id):
        handle = self.pending.pop(folder_id, None)
        if handle is not None:
            self.cancel(handle)

    def changed(self, folder_id):
        if self.ignored(folder_id):
            return
        self.discard(folder_id)
        def fire():
            self.pending.pop(folder_id, None)
            if not self.ignored(folder_id):
                self.enqueue(folder_id, "local-change")
            return False
        self.pending[folder_id] = self.schedule(max(1, int(self.seconds * 1000)), fire)

    def begin(self, folder_id):
        self.discard(folder_id)
        self.running.add(folder_id)

    def end(self, folder_id):
        self.running.discard(folder_id)
        self.ignore_until[folder_id] = self.clock() + 5

    def close(self):
        for folder_id in list(self.pending):
            self.discard(folder_id)


class MonitorLimit(RuntimeError):
    pass


class FolderWatchers:
    def __init__(self, enqueue, schedule, cancel, *, monitor_factory=gio_monitor,
                 clock=time.monotonic, warn=None):
        self.schedule, self.cancel, self.clock = schedule, cancel, clock
        self.factory = monitor_factory
        self.warn = warn or logging.warning
        self.debounce = Debouncer(enqueue, schedule, cancel, clock)
        self.monitors = {}  # folder id -> {directory: Gio.FileMonitor}
        self.roots = {}
        self.excludes = []
        self.limit = 20000
        self.fallback = set()
        self.warned = set()
        self.rescans = {}
        self.signature = None
        self.peak_count = 0
        self.fallback_reasons = {}

    @property
    def count(self):
        return sum(len(monitors) for monitors in self.monitors.values())

    def configure(self, config):
        folders = [(f["id"], os.path.abspath(os.path.expanduser(f["local"])))
                   for f in config["folders"] if f["enabled"] and f["initialized"]]
        signature = (tuple(folders), tuple(config["excludes"]), config["max_monitors"], config["debounce_seconds"])
        if signature == self.signature:
            return
        self.close()
        self.signature = signature
        self.roots = {key: Path(path) for key, path in folders}
        self.excludes, self.limit = list(config["excludes"]), config["max_monitors"]
        self.debounce.seconds = config["debounce_seconds"]
        self.fallback.clear()
        self.fallback_reasons.clear()
        self.peak_count = 0
        started = self.clock()
        for folder_id, root in self.roots.items():
            self.monitors[folder_id] = {}
            self._extend(folder_id, root)
        logging.info(i18n.WATCH_STARTUP,
                     self.count, self.clock() - started, len(self.fallback))

    def _safe(self, folder_id, path):
        root = self.roots[folder_id]
        try:
            relative = path.relative_to(root)
        except ValueError:
            return False
        # Check every component, including the root, without following symlinks.
        return not any(p.is_symlink() for p in [root, path] + list(path.parents)[:len(relative.parts)])

    def _extend(self, folder_id, path):
        if folder_id in self.fallback:
            return
        try:
            self._add_tree(folder_id, path)
        except (OSError, MonitorLimit) as error:
            self._fallback(folder_id, "sınır" if isinstance(error, MonitorLimit) else "dizin erişimi")
        except Exception as error:
            # Gio raises GLib.Error (not OSError) on watch exhaustion/permissions.
            from gi.repository import GLib
            if not isinstance(error, GLib.Error):
                raise
            self._fallback(folder_id, "Gio izleyici hatası")

    def _add_tree(self, folder_id, path):
        pending = [path]
        monitors = self.monitors[folder_id]
        while pending:
            directory = pending.pop()
            if not self._safe(folder_id, directory):
                continue
            relative = directory.relative_to(self.roots[folder_id]).as_posix()
            if filters.excluded(relative, True, self.excludes):
                continue
            if directory not in monitors:
                if self.count >= self.limit:
                    raise MonitorLimit()
                try:
                    monitor = self.factory(directory)
                except (FileNotFoundError, NotADirectoryError):
                    if directory == self.roots[folder_id]:
                        raise
                    continue
                monitors[directory] = monitor
                self.peak_count = max(self.peak_count, self.count)
                monitor.connect("changed", lambda _, file, other, event, fid=folder_id:
                                self._changed(fid, file, other))
            try:
                with os.scandir(directory) as entries:
                    pending.extend(Path(entry.path) for entry in entries if entry.is_dir(follow_symlinks=False))
            except (FileNotFoundError, NotADirectoryError):
                if directory == self.roots[folder_id]:
                    raise
                monitors.pop(directory).cancel()

    def _drop_monitors(self, folder_id):
        for monitor in self.monitors.pop(folder_id, {}).values():
            monitor.cancel()

    def _fallback(self, folder_id, reason):
        self.fallback_reasons[folder_id] = reason
        self._drop_monitors(folder_id)
        self.debounce.discard(folder_id)
        self.fallback.add(folder_id)
        if folder_id not in self.warned:
            self.warned.add(folder_id)
            self.warn(i18n.WATCH_FALLBACK.format(id=folder_id))

    def _changed(self, folder_id, file, other):
        if (folder_id not in self.roots or folder_id not in self.monitors or
                folder_id in self.fallback or self.debounce.ignored(folder_id)):
            return
        relevant = False
        for file in (file, other):
            if file is None or file.get_path() is None:
                continue
            path = Path(file.get_path())
            if not self._safe(folder_id, path):
                continue
            directory = path.is_dir()
            relative = path.relative_to(self.roots[folder_id]).as_posix()
            if filters.excluded(relative, directory, self.excludes):
                continue
            relevant = True
            if directory:
                self._extend(folder_id, path)
            else:
                # Remove stale monitors after a directory is deleted or renamed.
                for watched in list(self.monitors.get(folder_id, {})):
                    if watched == path or path in watched.parents:
                        self.monitors[folder_id].pop(watched).cancel()
        if relevant and folder_id not in self.fallback:
            self.debounce.changed(folder_id)

    def begin(self, folder_id):
        self.debounce.begin(folder_id)
        handle = self.rescans.pop(folder_id, None)
        if handle is not None:
            self.cancel(handle)

    def end(self, folder_id):
        self.debounce.end(folder_id)
        if folder_id not in self.roots:
            return
        # Events during bisync are ignored; rebuild topology after the grace period
        # to include directories downloaded/created during the run.
        def rescan():
            self.rescans.pop(folder_id, None)
            if folder_id in self.fallback:
                return False
            self._drop_monitors(folder_id)
            self.monitors[folder_id] = {}
            self._extend(folder_id, self.roots[folder_id])
            return False
        self.rescans[folder_id] = self.schedule(5000, rescan)

    def close(self):
        self.debounce.close()
        for handle in self.rescans.values():
            self.cancel(handle)
        self.rescans.clear()
        for folder_id in list(self.monitors):
            self._drop_monitors(folder_id)
