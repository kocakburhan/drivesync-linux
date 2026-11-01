"""GI-free, read-only Nautilus decisions and mtime-cached JSON snapshots."""

from datetime import datetime
import json
import os
from pathlib import Path
import time
from urllib.parse import urlencode

from .filters import excluded as _excluded, _watch_pattern

OK = "emblem-drivesync-ok"
SYNC = "emblem-drivesync-sync"
ERROR = "emblem-drivesync-error"


def excluded(relative, is_directory, excludes):
    # The watcher deliberately over-includes unknown rclone syntax. Badges
    # instead fail closed: no claim of sync for potentially excluded content.
    return (_excluded(relative, is_directory, excludes)
            or any(_watch_pattern(pattern.strip("/")) is None for pattern in excludes))


def folder_excludes(config, folder):
    result = list(config.get("excludes", []))
    result += [p for p in folder.get("excludes", []) if p not in result]
    return result


def folder_for_path(path, folders):
    """Lexical containment, without disk I/O; prefer the most specific root."""
    path = Path(os.path.abspath(path))
    matches = []
    for folder in folders:
        root = Path(os.path.abspath(os.path.expanduser(folder["local"])))
        if path.is_relative_to(root):
            matches.append((len(root.parts), folder, path.relative_to(root).as_posix()))
    if not matches:
        return None, None
    _, folder, relative = max(matches, key=lambda match: match[0])
    return folder, relative


def emblem_for(path, is_directory, mtime, config, state):
    folder, relative = folder_for_path(path, config.get("folders", []))
    if (not folder or not folder.get("initialized") or not folder.get("enabled", True)
            or excluded(relative, is_directory, folder_excludes(config, folder))):
        return None
    item = state.get("folders", {}).get(folder["id"], {})
    if item.get("needs_resync") or item.get("status") in ("error", "blocked_delete", "needs_resync"):
        return ERROR
    if item.get("status") in ("syncing", "queued"):
        return SYNC
    if not item.get("last_success") or mtime is None:
        return None
    success = datetime.fromisoformat(item["last_success"])
    if success.tzinfo is None:
        return None
    return SYNC if mtime > success.timestamp() else OK


def drive_search_url(name):
    return "https://drive.google.com/drive/search?" + urlencode({"q": name})


class SnapshotCache:
    """Use from the main loop only. No writes, validation side effects or locks.

    Even stat is throttled; changed files are read at most once per second.
    Errors discard old snapshots so stale success badges cannot survive failure.
    """
    def __init__(self, paths, clock=time.monotonic):
        self.paths, self.clock = paths, clock
        self.checked = float("-inf")
        self.values = {}

    def read(self):
        timestamp = self.clock()
        if timestamp - self.checked < 1:
            return tuple(self.values.get(p, (None, {}))[1]
                         for p in (self.paths.config, self.paths.state))
        self.checked = timestamp
        result = []
        for path in (self.paths.config, self.paths.state):
            try:
                stat = path.stat()
                key = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
                old = self.values.get(path)
                if old and old[0] == key:
                    value = old[1]
                else:
                    # Bound accidental or malformed input, without reading huge files.
                    if stat.st_size > 4 * 1024 * 1024:
                        raise ValueError("Snapshot too large")
                    with path.open(encoding="utf-8") as stream:
                        value = json.load(stream)
                    if not isinstance(value, dict):
                        raise ValueError("Invalid snapshot")
                self.values[path] = key, value
            except Exception:
                value = {}
                self.values.pop(path, None)
            result.append(value)
        return tuple(result)
