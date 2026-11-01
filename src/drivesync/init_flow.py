"""Initialization commands, preview parsing, deletion confirmation and the local upload estimate."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re

from . import filters, i18n

# Thresholds for a "large" first upload, in one place. A folder is large when it
# has MORE than LARGE_FILE_COUNT files or MORE than LARGE_BYTES bytes.
LARGE_FILE_COUNT = 20000
LARGE_BYTES = 10 * 1024 ** 3
LARGEST_SUBFOLDERS = 5


@dataclass(frozen=True)
class Plan:
    to_remote: int
    to_local: int
    deletes: int
    unknown: int
    actions: tuple
    size: dict | None = None

    @property
    def summary(self):
        return i18n.INIT_SUMMARY.format(to_remote=self.to_remote, to_local=self.to_local, deletes=self.deletes)

    def can_start(self, folder_name, typed=""):
        return self.unknown == 0 and (self.deletes == 0 or typed == folder_name)


def _valid_size(size):
    def count(value):
        return type(value) is int and value >= 0
    if (not isinstance(size, dict) or not count(size.get("bytes")) or not count(size.get("files"))
            or not count(size.get("estimated_seconds")) or type(size.get("large")) is not bool
            or type(size.get("assumed_bytes_per_second")) not in (int, float)
            or size["assumed_bytes_per_second"] <= 0):
        return False
    subfolders = size.get("largest_subfolders")
    return (isinstance(subfolders, list) and len(subfolders) <= LARGEST_SUBFOLDERS and all(
        isinstance(item, dict) and isinstance(item.get("name"), str) and count(item.get("bytes"))
        and count(item.get("files")) for item in subfolders))


def parse_plan(output):
    try:
        value = json.loads(output)
        counts = [value[key] for key in ("to_remote", "to_local", "deletes", "unknown")]
        actions = value["actions"]
        if (any(type(n) is not int or n < 0 for n in counts) or not isinstance(actions, list)
                or len(actions) > 50 or any(not isinstance(a, str) for a in actions)):
            raise ValueError
        size = value.get("size")
        if size is not None and not _valid_size(size):
            raise ValueError
        return Plan(*counts, tuple(actions), size=size)
    except (ValueError, KeyError, TypeError):
        raise ValueError(i18n.INIT_PLAN_INVALID) from None


def init_label(folder, state):
    if folder.get("enabled", True) and state.get("status") == "needs_approval":
        return i18n.INIT_REAPPROVE
    if (not folder.get("enabled", True) or state.get("status") in ("syncing", "queued", "init_queued")
            or (folder.get("initialized") and not state.get("needs_resync"))):
        return None
    return i18n.INIT_RESYNC if folder.get("initialized") else i18n.INIT_START


def preview_command(cli, folder_id):
    return [cli, "init", folder_id, "--dry-run", "--json"]


def submit_command(cli, folder_id, kind="init"):
    """Ask the CLI to persist the approval and hand the job to the daemon."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", folder_id) or kind not in ("init", "resync"):
        raise ValueError(i18n.INIT_PLAN_INVALID)
    # Both GUI and CLI callers have already obtained explicit user approval.
    return [cli, kind, folder_id, "--yes", "--json"]


def approved_plan(plan):
    """The counts the user approved, as stored in folders.<id>.init_request."""
    return {"to_remote": plan["to_remote"], "to_local": plan["to_local"], "deletes": plan["deletes"]}


def resume_is_safe(fresh, approved):
    """A fresh dry-run may resume only if it carries no new risk.

    Remaining uploads are fine (uploaded files are skipped); any delete, an
    unknown action or more downloads than approved require a new approval.
    """
    return fresh.deletes == 0 and fresh.unknown == 0 and fresh.to_local <= approved["to_local"]


def is_large(files, total_bytes):
    return files > LARGE_FILE_COUNT or total_bytes > LARGE_BYTES


def _tree(directory, relative, excludes):
    """Bytes and files below one directory; excluded paths and symlinks are skipped."""
    total_bytes = files = 0
    stack = [(directory, relative)]
    while stack:
        current, prefix = stack.pop()
        try:
            with os.scandir(current) as scan:
                entries = list(scan)
        except OSError:
            continue  # rclone reports unreadable folders during the real run
        for entry in entries:
            rel = prefix + "/" + entry.name
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if not filters.excluded(rel, True, excludes):
                        stack.append((entry.path, rel))
                elif entry.is_file(follow_symlinks=False) and not filters.excluded(rel, False, excludes):
                    total_bytes += entry.stat(follow_symlinks=False).st_size
                    files += 1
            except OSError:
                continue
    return total_bytes, files


def measure(local, excludes):
    """Local size estimate of what an upload would send, with largest immediate subfolders.

    Uses the global exclude patterns through the same helper as the watcher. Its
    matching is conservative for unknown syntax, so the estimate may be slightly high.
    """
    root = Path(os.path.expanduser(local))
    total_bytes = files = 0
    subfolders = []
    with os.scandir(root) as scan:
        entries = list(scan)
    for entry in entries:
        if entry.is_symlink():
            continue
        if entry.is_dir(follow_symlinks=False):
            if filters.excluded(entry.name, True, excludes):
                continue
            size, count = _tree(entry.path, entry.name, excludes)
            total_bytes += size
            files += count
            if count:
                subfolders.append(dict(name=entry.name, bytes=size, files=count))
        elif entry.is_file(follow_symlinks=False) and not filters.excluded(entry.name, False, excludes):
            total_bytes += entry.stat(follow_symlinks=False).st_size
            files += 1
    subfolders.sort(key=lambda item: (-item["bytes"], item["name"]))
    return total_bytes, files, subfolders[:LARGEST_SUBFOLDERS]


def size_report(local, excludes, bytes_per_second):
    """The "size" object of the dry-run JSON; durations use the assumed upload speed."""
    total_bytes, files, subfolders = measure(local, excludes)
    return dict(bytes=total_bytes, files=files, estimated_seconds=int(total_bytes / bytes_per_second),
                assumed_bytes_per_second=bytes_per_second, large=is_large(files, total_bytes),
                largest_subfolders=subfolders)
