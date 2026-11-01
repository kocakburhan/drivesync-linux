"""Pure window presentation and configuration edits, independent of GI."""

from copy import deepcopy
from dataclasses import dataclass
from pathlib import PurePath
import re

from . import __version__, config, i18n, updates
from .presentation import present, relative_time, progress_text


@dataclass(frozen=True)
class FolderRow:
    id: str
    title: str
    subtitle: str
    icon: str
    hint: str
    can_sync: bool
    status: str
    fraction: float | None


def folder_row(folder, state, now=None):
    status = state.get("status", "idle")
    if not folder.get("enabled", True):
        status = "disabled"
    elif status in ("init_queued", "needs_approval"):
        pass
    elif status == "syncing":
        status = "syncing" if folder.get("initialized", False) else "initial_syncing"
    elif not folder.get("initialized", False):
        status = "not_initialized"
    elif state.get("needs_resync"):
        status = "needs_resync"
    icon = {"ok": "ok", "syncing": "syncing", "initial_syncing": "syncing", "paused": "paused",
            "offline": "offline", "error": "error", "blocked_delete": "error", "needs_approval": "error",
            "needs_resync": "error"}.get(status, "neutral")
    hint = ""
    if (state.get("status") not in ("syncing", "init_queued")
            and (not folder.get("initialized", False) or state.get("needs_resync"))):
        hint = i18n.INIT_HINT.format(id=folder["id"])
        if state.get("needs_resync"):
            hint += "\n" + i18n.RESYNC_HINT.format(id=folder["id"])
    remote = folder.get("remote_path", "Linux-PC/" + folder["remote_name"]).split(":", 1)[-1]
    progress = state.get("progress") if icon == "syncing" else None
    subtitle = i18n.ROW_STATUS.format(status=i18n.STATUSES.get(status, i18n.STATUSES["idle"]),
                                    time=relative_time(state.get("last_success"), now))
    if progress:
        subtitle = i18n.STATUSES[status] + " · " + progress_text(progress)
    return FolderRow(folder["id"], f'{PurePath(folder["local"]).name} ↔ {remote}',
                     subtitle,
                     icon, hint, folder.get("enabled", True) and folder.get("initialized", False)
                     and not state.get("needs_resync")
                     and status not in ("blocked_delete", "syncing", "init_queued", "needs_approval"),
                     status, progress["percent"] / 100 if progress else None)


def overall_status(status, now=None):
    view = present(status, now)
    return i18n.APP_CURRENT.format(time=view.text.split(i18n.LAST_SYNC_SEPARATOR)[-1]) if view.icon == "ok" else view.text


def update_notice(status, current=__version__):
    """The newer release the daemon has seen, or None."""
    latest = ((status or {}).get("update") or {}).get("latest")
    return latest if updates.is_newer(latest, current) else None


def recent_conflicts_visible(status):
    return bool((status or {}).get("recent_conflicts"))


def sync_busy(status, requested=()):
    """Include optimistic local requests and activity from any status source."""
    return bool(status is not None and (requested or any(
        item.get("status") in ("queued", "syncing")
        for item in status.get("folders", {}).values())))


def problem_summary(status):
    problems = set()
    if status.get("daemon", {}).get("status") == "error" and status["daemon"].get("last_error"):
        problems.add(status["daemon"]["last_error"])
    for folder in status.get("folder_config", []):
        state = status.get("folders", {}).get(folder["id"], {})
        if state.get("needs_resync") or state.get("status") == "needs_resync":
            problems.add(i18n.BANNER_RESYNC)
        if state.get("status") == "blocked_delete":
            problems.add(i18n.DELETE_BLOCKED)
        elif state.get("status") == "error":
            problems.add(i18n.SYNC_ERROR)
    return " ".join(sorted(problems))


def new_folder(data, local, remote_name, *, exists, symlink, canonical, configured_roots):
    """Validate an already inspected filesystem snapshot; never access the filesystem."""
    if not exists or symlink:
        raise config.ConfigError(i18n.LOCAL_UNSAFE)
    root = PurePath(canonical)
    if (not root.is_absolute() or root == PurePath("/") or
            any(root == PurePath(p) or root in PurePath(p).parents or PurePath(p) in root.parents
                for p in configured_roots)):
        raise config.ConfigError(i18n.FOLDER_OVERLAP)
    if not config.component(remote_name) or remote_name != remote_name.strip():
        raise config.ConfigError(i18n.REMOTE_UNSAFE)
    if any(f["remote_name"] == remote_name for f in data["folders"]):
        raise config.ConfigError(i18n.REMOTE_DUPLICATE)
    base = re.sub(r"[^a-z0-9_-]+", "-", remote_name.lower()).strip("-") or "folder"
    identifier, suffix = base, 2
    ids = {f["id"] for f in data["folders"]}
    while identifier in ids:
        identifier = f"{base}-{suffix}"
        suffix += 1
    result = deepcopy(data)
    result["folders"].append(dict(id=identifier, local=local, remote_name=remote_name,
                                  enabled=True, initialized=False))
    return result


def remove_folder(data, folder_id):
    result = deepcopy(data)
    result["folders"] = [f for f in result["folders"] if f["id"] != folder_id]
    return result


def folder_excludes_edit(data, folder_id, excludes):
    result = deepcopy(data)
    folder = config.folder_by_id(result, folder_id)
    patterns = []
    for pattern in excludes:
        pattern = pattern.strip()
        if pattern and pattern not in patterns:
            patterns.append(pattern)
    if patterns:
        folder["excludes"] = patterns
    else:
        folder.pop("excludes", None)
    return config.validate(result)


def exclude_added(data, pattern, folder_id=None):
    """Add one exclude pattern, kept once.

    With folder_id the pattern goes to that folder's own excludes, so only that
    folder's filters (and resync requirement) change; otherwise to the global list.
    """
    if folder_id is None:
        result = deepcopy(data)
        if pattern not in result["excludes"]:
            result["excludes"].append(pattern)
        return config.validate(result)
    folder = config.folder_by_id(data, folder_id)
    return folder_excludes_edit(data, folder_id, list(folder.get("excludes", [])) + [pattern])


def settings_edit(data, poll, debounce, max_delete, excludes, *, bwlimit=None,
                  pause_on_battery=None, pause_on_metered=None, conflict_policy=None):
    result = deepcopy(data)
    result.update(poll_interval_minutes=poll, debounce_seconds=debounce,
                  max_delete_percent=max_delete, excludes=list(excludes))
    for key, value in (("bwlimit", None if bwlimit is None else bwlimit.strip()),
                       ("pause_on_battery", pause_on_battery), ("pause_on_metered", pause_on_metered),
                       ("conflict_policy", conflict_policy)):
        if value is not None:
            result[key] = value
    return config.validate(result)
