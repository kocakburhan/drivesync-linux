"""Cheap remote change detection: `rclone backend query` plus a Drive directory-ID cache.

A full bisync lists the whole remote folder. This module asks Drive only for items
created or modified since the last check and maps them to configured folders through
their parent IDs. Failures never block syncing: callers fall back to a full bisync.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json

from gi.repository import Gio, GLib

from .config import remote_path
from .paths import write_json
from .rclone import executable

# Drive search can lag several minutes behind uploads; re-check that window every time.
SKEW = timedelta(minutes=10)
QUERY_TIMEOUT = 180
LIST_TIMEOUT = 600
_NETWORK = ["--contimeout", "15s", "--timeout", "60s", "--retries", "1", "--low-level-retries", "1"]


class RemoteCheckError(Exception):
    pass


def format_time(moment):
    """RFC 3339 UTC, whole seconds, as the Drive query language expects."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(text):
    """Parse our ISO state values and Drive's `2024-02-02T10:40:02.874Z`; None when unusable."""
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def query_string(since):
    stamp = format_time(since)
    # createdTime matters: rclone and browsers keep the file's own mtime as modifiedTime,
    # so a freshly uploaded old file is only recognisable by its creation time.
    return f"(modifiedTime > '{stamp}' or createdTime > '{stamp}') and trashed = false"


def query_command(config, since):
    return [executable(config), "backend", "query", config["remote"], query_string(since)] + _NETWORK


def list_command(config):
    return [executable(config), "lsjson", remote_path(config), "--dirs-only", "-R", "--fast-list",
            "--no-modtime", "--no-mimetype"] + _NETWORK


def parse_query(text):
    text = text.strip()
    if not text or text == "null":
        return []
    try:
        value = json.loads(text)
    except ValueError:
        raise RemoteCheckError("query output is not JSON") from None
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RemoteCheckError("query output is not a list")
    return value


def parse_dirs(text, remote_name):
    """IDs of `remote_name` and every directory below it from `lsjson --dirs-only -R` of the root."""
    text = text.strip()
    try:
        entries = json.loads(text) if text and text != "null" else []
    except ValueError:
        raise RemoteCheckError("lsjson output is not JSON") from None
    if not isinstance(entries, list):
        raise RemoteCheckError("lsjson output is not a list")
    ids = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("ID"), str):
            continue
        path = entry.get("Path")
        if isinstance(path, str) and (path == remote_name or path.startswith(remote_name + "/")):
            ids.add(entry["ID"])
    return sorted(ids)


def index_path(paths, folder_id):
    return paths.remote_index / f"{folder_id}.json"


def load_index(paths, folder_id):
    """Set of directory IDs, or None when there is no usable cache."""
    try:
        value = json.loads(index_path(paths, folder_id).read_text(encoding="utf-8"))
        return set(value["ids"]) if isinstance(value["ids"], list) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_index(paths, folder_id, ids):
    paths.remote_index.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_json(index_path(paths, folder_id), {"version": 1, "updated_at": datetime.now(timezone.utc).isoformat(),
                                              "ids": sorted(ids)})


@dataclass
class Attribution:
    changed: set = field(default_factory=set)   # folder ids with attributed changes
    loose: set = field(default_factory=set)     # folders touched by items without parents
    unknown: set = field(default_factory=set)   # parent ids found in no cache


def attribute(items, indexes, since, foreign=frozenset()):
    """Map changed Drive items to folder ids.

    indexes: folder id -> set of directory IDs; since: folder id -> datetime (folders being checked).
    An item counts for a folder only if it is newer than that folder's own last check.
    """
    result = Attribution()
    for item in items:
        times = [parse_time(item.get(key)) for key in ("modifiedTime", "createdTime")]
        newest = max((t for t in times if t), default=None)
        parents = item.get("parents")
        parents = [p for p in parents if isinstance(p, str)] if isinstance(parents, list) else []
        refs = set(parents) | ({item["id"]} if isinstance(item.get("id"), str) else set())
        matched = False
        for folder_id, ids in indexes.items():
            if folder_id not in since or not refs & ids:
                continue
            matched = True
            if newest is None or newest > since[folder_id]:
                result.changed.add(folder_id)
        if matched:
            continue
        if not parents:
            result.loose.update(f for f in since if newest is None or newest > since[f])
        else:
            result.unknown.update(p for p in parents if p not in foreign)
    return result


def run_capture(command, timeout, done):
    """Run a child without blocking; done(exit_code, stdout) is called exactly once on the main loop."""
    try:
        process = Gio.Subprocess.new(command, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
    except GLib.Error:
        GLib.idle_add(lambda: (done(-1, ""), GLib.SOURCE_REMOVE)[1])
        return None
    state = {"timer": None}

    def expire():
        state["timer"] = None
        process.force_exit()
        return GLib.SOURCE_REMOVE

    def finished(proc, result):
        if state["timer"] is not None:
            GLib.source_remove(state["timer"])
        try:
            _, out, _ = proc.communicate_utf8_finish(result)
        except GLib.Error:
            done(-1, "")
            return
        done(proc.get_exit_status() if proc.get_if_exited() else -1, out or "")

    state["timer"] = GLib.timeout_add_seconds(timeout, expire)
    process.communicate_utf8_async(None, None, finished)
    return process


@dataclass
class Outcome:
    ok: bool
    changed: set = field(default_factory=set)
    loose: set = field(default_factory=set)
    error: str = ""


class RemoteChecker:
    def __init__(self, paths, get_config, runner=run_capture):
        self.paths = paths
        self.get_config = get_config
        self.runner = runner
        self.running = False
        self.stale = True
        self.foreign = set()   # parent IDs seen after a refresh that belong to none of our folders
        self.process = None
        self._since = {}
        self._done = None

    def mark_stale(self):
        self.stale = True

    def cancel(self):
        if self.process:
            self.process.force_exit()

    def check(self, folders, since, done):
        """folders: folder dicts to check; since: id -> datetime; done(Outcome)."""
        self.running = True
        self._since = {f["id"]: since[f["id"]] for f in folders}
        self._done = done
        floor = min(self._since.values())
        if self.stale or any(load_index(self.paths, f["id"]) is None for f in folders):
            self._refresh(lambda: self._query(floor, refreshed=True))
        else:
            self._query(floor, refreshed=False)

    def _finish(self, outcome):
        self.running = False
        self.process = None
        self._done(outcome)

    def _fail(self, text):
        self._finish(Outcome(False, error=text))

    def _refresh(self, then):
        config = self.get_config()

        def listed(code, out):
            self.process = None
            if code:
                return self._fail(f"lsjson exit {code}")
            try:
                for folder in config["folders"]:
                    save_index(self.paths, folder["id"], parse_dirs(out, folder["remote_name"]))
            except (RemoteCheckError, OSError) as error:
                return self._fail(str(error))
            self.stale = False
            self.foreign = set()
            then()
        self.process = self.runner(list_command(config), LIST_TIMEOUT, listed)

    def _query(self, floor, refreshed):
        config = self.get_config()

        def queried(code, out):
            self.process = None
            if code:
                return self._fail(f"query exit {code}")
            try:
                items = parse_query(out)
            except RemoteCheckError as error:
                return self._fail(str(error))
            self._attribute(items, refreshed)
        self.process = self.runner(query_command(config, floor), QUERY_TIMEOUT, queried)

    def _attribute(self, items, refreshed):
        indexes = {fid: load_index(self.paths, fid) or set() for fid in self._since}
        result = attribute(items, indexes, self._since, self.foreign)
        if result.unknown and not refreshed:
            return self._refresh(lambda: self._attribute(items, True))
        self.foreign |= result.unknown   # outside our tree; do not refresh again for them
        self._finish(Outcome(True, result.changed, result.loose))
