"""Deterministic rclone filters and fail-closed baseline tracking."""

import hashlib
from functools import lru_cache
import fnmatch
from pathlib import PurePosixPath
import re

from . import i18n
from .config import pair_hash
from .paths import atomic_write
from .state import folder_state, unlocked_folders


def render(excludes):
    # First-match wins: protect the access sentinel even from broad patterns.
    lines = ["+ /.drivesync-check"]
    for pattern in excludes:
        if pattern.endswith("/"):
            name = pattern.rstrip("/")
            if not name.startswith("/"):
                lines.append(f"- **/{name}/**")
            lines.append(f"- {name}/**")
        else:
            lines.append(f"- {pattern}")
    return "\n".join(lines) + "\n"


def prepare(paths, config, store, *, owned_folder_id=None):
    """Caller must hold engine.lock, so an active run's filters never change."""
    content = render(config["excludes"])
    digest = hashlib.sha256(content.encode()).hexdigest()
    if not paths.filters.exists() or paths.filters.read_text(encoding="utf-8") != content:
        atomic_write(paths.filters, content)

    def reconcile(value):
        old_hash = value.get("filters_sha256")
        for folder in config["folders"]:
            if folder["id"] not in available:
                continue
            item = value["folders"].setdefault(folder["id"], folder_state())
            fingerprint = pair_hash(config, folder)
            changed_pair = item.get("pair_sha256", fingerprint) != fingerprint
            if folder["initialized"] and (old_hash != digest or changed_pair):
                item.update(needs_resync=True, status="error", dry_run_ok_at=None,
                            last_error=i18n.PAIR_CHANGED if changed_pair else i18n.FILTER_CHANGED)
            item["pair_sha256"] = fingerprint
            if not folder["initialized"]:
                item["needs_resync"] = False
            if not folder["enabled"]:
                item["status"] = "disabled"
            elif not folder["initialized"] and not item["needs_resync"]:
                item["status"] = "not_initialized"
            elif item["needs_resync"] and item["status"] == "disabled":
                item["status"] = "error"
            elif folder["initialized"] and item["status"] in ("disabled", "not_initialized"):
                item["status"] = "ok"
        # Keep the previous baseline until every folder can be reconciled, so
        # a skipped folder still detects a filter change after its lock is freed.
        if len(available) == len(config["folders"]):
            value["filters_sha256"] = digest
    with unlocked_folders(paths, (f["id"] for f in config["folders"]),
                          owned_folder_id=owned_folder_id) as available:
        store.update(reconcile)
    return digest


def excluded(relative, is_directory, excludes):
    """Conservative rclone glob subset for watching, not the sync filter authority.

    Unknown syntax keeps extra watches rather than risking missed changes. Test
    ancestors too, so events below an excluded directory are ignored as well.
    """
    path = PurePosixPath(relative)
    if str(path) in (".", ".drivesync-check"):
        return False
    candidates = [(str(parent), True) for parent in reversed(path.parents) if str(parent) != "."]
    candidates.append((str(path), is_directory))
    for name, directory in candidates:
        for pattern in excludes:
            if pattern.endswith("/") and not directory:
                continue
            pattern = pattern.rstrip("/")
            anchored = pattern.startswith("/")
            pattern = pattern.lstrip("/")
            expression = _watch_pattern(pattern)
            if expression is None:
                continue
            target = name if anchored or "/" in pattern else name.rsplit("/", 1)[-1]
            if expression.fullmatch(target):
                return True
    return False


@lru_cache(maxsize=512)
def _watch_pattern(pattern):
    # rclone supports additional syntax; retaining watches for it is safe.
    if any(c in pattern for c in "{}\\"):
        return None
    result, index = "", 0
    while index < len(pattern):
        if pattern[index:index + 3] == "**/":
            result += "(?:.*/)?"
            index += 3
        elif pattern[index:index + 2] == "**":
            result += ".*"
            index += 2
        elif pattern[index] == "*":
            result += "[^/]*"
            index += 1
        elif pattern[index] == "?":
            result += "[^/]"
            index += 1
        elif pattern[index] == "[":
            end = pattern.find("]", index + 1)
            if end < 0:
                return None
            result += fnmatch.translate(pattern[index:end + 1])[4:-3]
            index = end + 1
        else:
            result += re.escape(pattern[index])
            index += 1
    try:
        return re.compile(result)
    except re.error:
        return None
