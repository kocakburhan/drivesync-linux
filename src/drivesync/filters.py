"""Deterministic rclone filters and fail-closed baseline tracking."""

import hashlib
from functools import lru_cache
import fnmatch
from pathlib import PurePosixPath
import re

from . import i18n
from . import config as configuration
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


def digest_of(excludes):
    return hashlib.sha256(render(excludes).encode()).hexdigest()


def _legacy_md5(paths, config):
    """Old shared rclone hash, only if the old file holds exactly the global rules."""
    try:
        old = paths.legacy_filters.read_text(encoding="utf-8")
        sidecar = paths.legacy_filters.with_name("filters.txt.md5").read_text(encoding="utf-8")
    except OSError:
        return None
    return sidecar if old == render(config["excludes"]) and sidecar.strip() else None


def prepare(paths, config, store, *, owned_folder_id=None):
    """Write one filters file per folder; return {folder id: digest}.

    Caller must hold engine.lock, so an active run's filters never change.
    The digest is the hash of the file content, which for a folder without own
    patterns equals the digest of the former single global file. That is what
    keeps existing installs from a needless resync (rclone bisync itself hashes
    the filter rules, not the path of the file).
    """
    digests, contents = {}, {}
    for folder in config["folders"]:
        content = render(configuration.effective_excludes(config, folder))
        contents[folder["id"]] = content
        digests[folder["id"]] = hashlib.sha256(content.encode()).hexdigest()
    global_digest = digest_of(config["excludes"])
    legacy_md5 = _legacy_md5(paths, config)
    for folder_id, content in contents.items():
        target = paths.filters_for(folder_id)
        try:
            current = target.read_text(encoding="utf-8")
        except OSError:
            current = None
        if current != content:
            atomic_write(target, content)
        # rclone bisync keeps the hash of the filter rules in "<filters file>.md5"
        # (keyed by the file path). Carry the old one over for an unchanged rule set,
        # otherwise the path move alone would demand --resync.
        sidecar = target.with_name(target.name + ".md5")
        if legacy_md5 is not None and not sidecar.exists():
            atomic_write(sidecar, legacy_md5)
    if paths.filters_dir.is_dir():
        for stale in paths.filters_dir.glob("*.txt*"):
            if stale.name.removesuffix(".md5").removesuffix(".txt") not in contents:
                stale.unlink(missing_ok=True)
    for old in (paths.legacy_filters, paths.legacy_filters.with_name("filters.txt.md5")):
        old.unlink(missing_ok=True)

    def reconcile(value):
        legacy_hash = value.get("filters_sha256")
        for folder in config["folders"]:
            if folder["id"] not in available:
                continue
            item = value["folders"].setdefault(folder["id"], folder_state())
            digest = digests[folder["id"]]
            # First run after the upgrade: the old global hash is the baseline.
            old_hash = item.get("filters_sha256") or legacy_hash
            fingerprint = pair_hash(config, folder)
            changed_pair = item.get("pair_sha256", fingerprint) != fingerprint
            if folder["initialized"] and (old_hash != digest or changed_pair):
                item.update(needs_resync=True, status="error", dry_run_ok_at=None,
                            last_error=i18n.PAIR_CHANGED if changed_pair else i18n.FILTER_CHANGED)
            item["pair_sha256"] = fingerprint
            item["filters_sha256"] = digest
            if not folder["initialized"]:
                item["needs_resync"] = False
            if not folder["enabled"]:
                item["status"] = "disabled"
            elif (not folder["initialized"] and not item["needs_resync"]
                  and not (item.get("init_request") and item["status"] in ("init_queued", "needs_approval"))):
                item["status"] = "not_initialized"
            elif item["needs_resync"] and item["status"] == "disabled":
                item["status"] = "error"
            elif folder["initialized"] and item["status"] in ("disabled", "not_initialized"):
                item["status"] = "ok"
        # Keep the previous legacy baseline until every folder was reconciled, so
        # a skipped folder (no own hash yet) still detects a change later.
        if len(available) == len(config["folders"]):
            value["filters_sha256"] = global_digest
    with unlocked_folders(paths, (f["id"] for f in config["folders"]),
                          owned_folder_id=owned_folder_id) as available:
        store.update(reconcile)
    return digests


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
