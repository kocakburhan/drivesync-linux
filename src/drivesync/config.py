"""Versioned configuration, preserving extensions on every save."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

from . import i18n
from .paths import write_json


class ConfigError(ValueError):
    pass


DEFAULT_EXCLUDES = ["node_modules/", ".venv/", "venv/", "__pycache__/", "build/",
                    "dist/", ".git/", "*.tmp", "*.swp", "*~", ".~lock.*", "~$*"]


def defaults():
    binary = shutil.which("rclone") or str(Path.home() / ".local/bin/rclone")
    return {"version": 1, "rclone_path": str(Path(binary).resolve()), "remote": "gdrive:",
            "root_folder": "Linux-PC", "language": "auto", "poll_interval_minutes": 5,
            "full_sync_interval_minutes": 60, "debounce_seconds": 10,
            "max_delete_percent": 25, "max_monitors": 20000, "paused": False,
            "transfers": 4, "init_transfers": 8, "checkers": 8,
            "bwlimit": "", "pause_on_battery": False, "pause_on_metered": True,
            "pause_stops_running": False,
            "init_extra_flags": ["--drive-pacer-min-sleep", "10ms", "--drive-pacer-burst", "200"],
            "excludes": list(DEFAULT_EXCLUDES), "folders": []}


def invalid(field):
    raise ConfigError(i18n.CONFIG_ERROR.format(field=field))


def clean_text(value):
    return isinstance(value, str) and bool(value.strip()) and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def component(value):
    return clean_text(value) and value not in (".", "..") and not any(c in value for c in "/\\:")


_SIZE = r"\d+(?:\.\d+)?(?:[bB]|[kKmMgGtTpP](?:i?[bB]?)?)?"
_RATE = rf"(?:{_SIZE}|[oO][fF][fF])"
_LIMIT = rf"{_RATE}(?::{_RATE})?"
_ENTRY = rf"(?:(?:[Mm]on|[Tt]ue|[Ww]ed|[Tt]hu|[Ff]ri|[Ss]at|[Ss]un)-)?(?:[01]\d|2[0-3]):[0-5]\d,{_LIMIT}"
BWLIMIT_RE = re.compile(rf"(?:{_LIMIT}|{_ENTRY}(?: +{_ENTRY})*)")


def valid_bwlimit(value):
    """Conservative local check of rclone's --bwlimit syntax; "" means unlimited.

    Accepted: ``1M`` / ``off`` (sizes b, k, M, G, T, P with optional i/B, decimals allowed),
    an upload:download split ``10M:off``, and timetables of space-separated
    ``[Day-]HH:MM,limit`` entries such as ``08:00,1M 23:00,off`` or ``Mon-08:00,512k:2M``.
    """
    return (isinstance(value, str) and len(value) <= 200
            and (value == "" or BWLIMIT_RE.fullmatch(value) is not None))


def validate(data):
    if not isinstance(data, dict):
        invalid("config.json")
    result = defaults()
    result.update(deepcopy(data))
    if type(result["version"]) is not int or result["version"] != 1:
        invalid("version")
    if not clean_text(result["rclone_path"]):
        invalid("rclone_path")
    if not isinstance(result["remote"], str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_. -]*:", result["remote"]):
        invalid("remote")
    if not component(result["root_folder"]):
        invalid("root_folder")
    if result["language"] not in ("auto", "tr", "en"):
        invalid("language")
    for field, minimum, maximum in (("poll_interval_minutes", 0, None),
                                     ("full_sync_interval_minutes", 0, None),
                                     ("debounce_seconds", 0, None),
                                     ("max_delete_percent", 0, 100)):
        value = result[field]
        if type(value) not in (int, float) or not minimum <= value < float("inf"):
            invalid(field)
        if field in ("poll_interval_minutes", "full_sync_interval_minutes") and value == 0:
            invalid(field)
        if maximum is not None and value > maximum:
            invalid(field)
    for field in ("max_monitors", "transfers", "init_transfers", "checkers"):
        if type(result[field]) is not int or result[field] < 1:
            invalid(field)
    if (not isinstance(result["init_extra_flags"], list) or
            any(not clean_text(flag) or flag != flag.strip() for flag in result["init_extra_flags"])):
        invalid("init_extra_flags")
    if not valid_bwlimit(result["bwlimit"]):
        invalid("bwlimit")
    for field in ("paused", "pause_on_battery", "pause_on_metered", "pause_stops_running"):
        if type(result[field]) is not bool:
            invalid(field)
    if not isinstance(result["excludes"], list):
        invalid("excludes")
    for pattern in result["excludes"]:
        if not clean_text(pattern) or pattern != pattern.strip():
            invalid("excludes")
    if not isinstance(result["folders"], list):
        invalid("folders")
    ids, locals_, remotes = set(), [], set()
    for folder in result["folders"]:
        if not isinstance(folder, dict):
            invalid("folders")
        folder.setdefault("enabled", True)
        folder.setdefault("initialized", False)
        if not isinstance(folder.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]+", folder["id"]):
            invalid("folders.id")
        if folder["id"] in ids:
            invalid("folders.id")
        ids.add(folder["id"])
        if not clean_text(folder.get("local")) or not Path(os.path.expanduser(folder["local"])).is_absolute():
            invalid("folders.local")
        if not component(folder.get("remote_name")) or folder["remote_name"] in remotes:
            invalid("folders.remote_name")
        remotes.add(folder["remote_name"])
        # Overlapping roots could independently delete or overwrite the same data.
        local = Path(os.path.realpath(os.path.expanduser(folder["local"])))
        if local == Path("/") or any(local == p or local in p.parents or p in local.parents for p in locals_):
            invalid("folders.local")
        locals_.append(local)
        for key in ("enabled", "initialized"):
            if type(folder[key]) is not bool:
                invalid(f"folders.{key}")
        if "excludes" in folder:
            if not isinstance(folder["excludes"], list):
                invalid("folders.excludes")
            for pattern in folder["excludes"]:
                if not clean_text(pattern) or pattern != pattern.strip():
                    invalid("folders.excludes")
    return result


def load(paths, *, create=True):
    try:
        data = json.loads(paths.config.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = defaults()
        if create:
            save(paths, data)
    except (OSError, ValueError):
        raise ConfigError(i18n.READ_ERROR.format(path=paths.config)) from None
    return validate(data)


def save(paths, data):
    write_json(paths.config, validate(data))


def edit_lock(paths):
    # Pause must remain responsive while a transfer holds the engine lock.
    from .state import FileLock
    return FileLock(paths.state_dir / "config.lock", blocking=True)


def folder_by_id(config, folder_id):
    for folder in config["folders"]:
        if folder["id"] == folder_id:
            return folder
    raise ConfigError(i18n.UNKNOWN_FOLDER.format(id=folder_id))


def effective_excludes(config, folder=None):
    """Global patterns followed by the folder's own, without duplicates."""
    result = list(config["excludes"])
    for pattern in (folder or {}).get("excludes", []):
        if pattern not in result:
            result.append(pattern)
    return result


def remote_path(config, folder=None):
    root = config["remote"] + config["root_folder"]
    return root if folder is None else root + "/" + folder["remote_name"]


def pair_hash(config, folder):
    pair = [os.path.abspath(os.path.expanduser(folder["local"])), remote_path(config, folder),
            os.path.expanduser(config["rclone_path"]), config["max_delete_percent"]]
    return hashlib.sha256(json.dumps(pair).encode()).hexdigest()
