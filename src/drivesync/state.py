"""Atomic state transactions and cross-process execution locks."""

from contextlib import contextmanager, ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
import time

from . import i18n
from .paths import write_json


def now():
    return datetime.now(timezone.utc).isoformat()


class StateError(ValueError):
    pass


class BusyError(RuntimeError):
    def __init__(self):
        super().__init__(i18n.BUSY)


class FileLock:
    def __init__(self, path, blocking=False):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BaseException as error:
            os.close(self.fd)
            self.fd = None
            if isinstance(error, BlockingIOError):
                raise BusyError() from None
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


@contextmanager
def execution_lock(paths, folder_id=None):
    # Global first, folder second: one ordering for daemon and direct CLI runs.
    with FileLock(paths.state_dir / "engine.lock"):
        if folder_id is None:
            yield
        else:
            with folder_lock(paths, folder_id):
                yield


def folder_lock(paths, folder_id):
    digest = hashlib.sha256(folder_id.encode()).hexdigest()[:24]
    return FileLock(paths.state_dir / f"folder-{digest}.lock")


@contextmanager
def unlocked_folders(paths, folder_ids, *, owned_folder_id=None):
    """Hold available folder locks through a state edit; skip external owners.

    Acquire before state.lock, following the same ordering as direct CLI runs.
    A caller already holding its folder lock must name it explicitly.
    """
    with ExitStack() as stack:
        available = set()
        for folder_id in folder_ids:
            if folder_id != owned_folder_id:
                try:
                    stack.enter_context(folder_lock(paths, folder_id))
                except BusyError:
                    continue
            available.add(folder_id)
        yield available


def empty_state():
    return {"daemon": {"status": "idle", "current_folder": None, "pid": None,
                       "started_at": None, "last_error": None}, "filters_sha256": None,
            "folders": {}, "recent_conflicts": []}


def folder_state():
    return {"status": "not_initialized", "last_sync_started": None,
            "last_sync_finished": None, "last_success": None, "last_exit_code": None,
            "last_error": None, "conflicts": 0, "needs_resync": False,
            "last_log": None, "last_remote_check": None, "last_full_sync": None,
            "dry_run_ok_at": None, "dry_run_filters_sha256": None,
            "progress": None}


class StateStore:
    def __init__(self, paths, changed=None, clock=time.monotonic):
        self.paths = paths
        self.changed = changed
        self.clock = clock
        self._progress_written = {}

    def _read(self):
        try:
            value = json.loads(self.paths.state.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or not isinstance(value.get("daemon"), dict):
                raise ValueError()
            if not isinstance(value.get("folders"), dict):
                raise ValueError()
            value["daemon"].setdefault("last_error", None)
            value.setdefault("recent_conflicts", [])
            if not isinstance(value["recent_conflicts"], list):
                raise ValueError()
            value["recent_conflicts"] = value["recent_conflicts"][-100:]
            for item in value["folders"].values():
                if not isinstance(item, dict):
                    raise ValueError()
                for key, default in folder_state().items():
                    item.setdefault(key, default)
            # Config is authoritative: an uninitialized pair has no baseline to resync.
            # Do not create config as a side effect of reading state.
            if self.paths.config.exists():
                from .config import ConfigError, load
                try:
                    folders = load(self.paths)["folders"]
                except ConfigError:
                    # A bad config must not prevent the daemon recording its config error.
                    folders = []
                for folder in folders:
                    item = value["folders"].get(folder["id"])
                    if (item and not folder["initialized"] and item["needs_resync"]
                            and item["status"] != "syncing"):
                        item["needs_resync"] = False
                        item["status"] = "not_initialized" if folder["enabled"] else "disabled"
            return value
        except FileNotFoundError:
            return empty_state()
        except (ValueError, OSError):
            raise StateError(i18n.STATE_ERROR) from None

    def read(self):
        return deepcopy(self._read())

    def update(self, mutate):
        with FileLock(self.paths.state_dir / "state.lock", blocking=True):
            value = self._read()
            before = deepcopy(value)
            mutate(value)
            different = value != before
            if different:
                write_json(self.paths.state, value)
        if different and self.changed:
            self.changed()
        return value

    def folder(self, folder_id, **fields):
        if "status" in fields and fields["status"] != "syncing":
            fields["progress"] = None
        if fields.get("progress", False) is None:
            self._progress_written.pop(folder_id, None)
        def mutate(value):
            value["folders"].setdefault(folder_id, folder_state()).update(fields)
        return self.update(mutate)

    def progress(self, folder_id, progress):
        """Publish live stats at most once per two seconds for each active folder."""
        moment = self.clock()
        if moment - self._progress_written.get(folder_id, float("-inf")) < 2:
            return False
        published = False
        def mutate(value):
            nonlocal published
            item = value["folders"].get(folder_id)
            if item and item["status"] == "syncing":
                item["progress"] = dict(progress, updated_at=now())
                published = True
        self.update(mutate)
        if published:
            self._progress_written[folder_id] = moment
        return published

    def daemon(self, **fields):
        return self.update(lambda value: value["daemon"].update(fields))

    def clear_conflicts(self):
        return self.update(lambda value: value.update(recent_conflicts=[]))
