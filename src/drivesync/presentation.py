"""Pure, language-aware tray presentation; no GI, filesystem or bus access."""

from dataclasses import dataclass
from datetime import datetime, timezone

from . import i18n

# Installed DriveSync icons first, followed by standard theme fallbacks.
ICONS = {
    "neutral": ("drivesync-idle-symbolic", "folder-symbolic", "folder"),
    "ok": ("drivesync-ok-symbolic", "emblem-ok-symbolic", "emblem-default", "folder"),
    "syncing": ("drivesync-syncing-symbolic", "emblem-synchronizing-symbolic", "view-refresh", "folder"),
    "paused": ("drivesync-paused-symbolic", "media-playback-pause-symbolic", "media-playback-pause", "folder"),
    "error": ("drivesync-error-symbolic", "dialog-warning-symbolic", "dialog-warning", "folder"),
    "offline": ("drivesync-offline-symbolic", "network-offline-symbolic", "network-offline", "folder"),
}


# AppIndicator hosts may not recolour symbolic icons on dark panels.
# Keep GTK's symbolic mapping above separate from the tray's explicit colours.
TRAY_ICONS = {
    key: (f"drivesync-tray-{'idle' if key == 'neutral' else key}", *fallbacks)
    for key, fallbacks in ICONS.items()
}
TRAY_FRAMES = tuple(
    (f"drivesync-tray-syncing-{frame}", f"drivesync-syncing-{frame}-symbolic",
     *TRAY_ICONS["syncing"])
    for frame in range(8)
)


def timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def relative_time(value, now=None):
    moment = timestamp(value)
    if moment is None:
        return i18n.NEVER
    now = now or datetime.now(timezone.utc)
    seconds = max(0, (now - moment).total_seconds())
    if seconds < 60:
        return i18n.JUST_NOW
    if seconds < 3600:
        return i18n.MINUTES_AGO.format(count=int(seconds // 60))
    if seconds < 86400:
        return i18n.HOURS_AGO.format(count=int(seconds // 3600))
    days = int(seconds // 86400)
    return i18n.YESTERDAY if days == 1 else i18n.DAYS_AGO.format(count=days)


def format_number(value, decimals=0):
    """Language-aware number: Turkish "1.234,5", English "1,234.5"."""
    text = f"{value:,.{decimals}f}"
    if i18n.current_language() == "tr":
        text = text.translate(str.maketrans({",": ".", ".": ","}))
    return text


turkish_number = format_number  # backwards-compatible alias


def eta_text(seconds):
    if seconds is None:
        return ""
    minutes = max(0, int(seconds)) // 60
    hours, minutes = divmod(minutes, 60)
    if hours:
        duration = i18n.DURATION_H.format(value=format_number(hours))
        if minutes:
            duration += " " + i18n.DURATION_M.format(value=minutes)
    elif minutes:
        duration = i18n.DURATION_M.format(value=minutes)
    else:
        duration = i18n.DURATION_S.format(value=int(max(0, seconds)))
    return i18n.ETA_LEFT.format(duration=duration)


def progress_text(progress, *, compact=False):
    if not progress:
        return ""
    percent = i18n.PERCENT.format(value=format_number(progress["percent"]))
    eta = eta_text(progress.get("eta_seconds"))
    if compact:
        return " · ".join(part for part in (percent, eta) if part)
    units = ("B", "KB", "MB", "GB", "TB")
    def scale(value):
        power = 0
        while value >= 1024 and power < 4:
            value /= 1024
            power += 1
        return power
    power = scale(progress["bytes_total"])
    divisor = 1024 ** power
    decimals = 1 if power else 0
    amount = (f'{format_number(progress["bytes_done"] / divisor, decimals)} / '
              f'{format_number(progress["bytes_total"] / divisor, decimals)} {units[power]}')
    speed = progress.get("speed", 0)
    power = scale(speed)
    rate = i18n.RATE.format(value=format_number(speed / 1024 ** power, 1 if power else 0),
                            unit=units[power])
    files = ""
    if progress.get("files_done") is not None and progress.get("files_total") is not None:
        files = i18n.FILES_PROGRESS.format(done=format_number(progress["files_done"]),
                                           total=format_number(progress["files_total"]))
    return " · ".join(part for part in (amount, percent, rate, eta, files) if part)


def overall_progress(status):
    syncing = [item for item in status.get("folders", {}).values() if item.get("status") == "syncing"]
    if not syncing or any(not item.get("progress") for item in syncing):
        return ""
    total = sum(item["progress"]["bytes_total"] for item in syncing)
    if not total:
        return ""
    done = sum(item["progress"]["bytes_done"] for item in syncing)
    return i18n.TOTAL_PROGRESS.format(
        percent=i18n.PERCENT.format(value=format_number(min(100, done / total * 100))))


def tray_frame(frame, syncing, animations=True):
    """Next pre-rendered frame, or None when a timer should be stopped."""
    return (frame + 1) % 8 if syncing and animations else None


@dataclass(frozen=True)
class Presentation:
    text: str
    icon: str
    paused: bool = False
    available: bool = True


def present(status=None, now=None):
    if status is None:
        return Presentation(i18n.TRAY_STOPPED, "error", available=False)
    daemon = status.get("daemon", {})
    state = daemon.get("status", "idle")
    paused = bool(status.get("paused") or state == "paused")
    folders = status.get("folders", {})
    configured = status.get("folder_config", [])
    # Direct CLI runs do not update daemon.current_folder/status.
    syncing_id = next((folder_id for folder_id, item in folders.items()
                       if item.get("status") == "syncing"), None)
    if syncing_id is not None:
        syncing = next((f for f in configured if f["id"] == syncing_id),
                       {"id": syncing_id, "initialized": True})
        text = (i18n.TRAY_SYNCING if syncing.get("initialized", False)
                else i18n.TRAY_INITIAL_SYNCING)
        line = text.format(name=syncing.get("remote_name", syncing["id"]))
        progress = progress_text(folders[syncing_id].get("progress"), compact=True)
        return Presentation(line + (" · " + progress if progress else ""),
                            "syncing", paused)
    if state == "error" and daemon.get("last_error"):
        return Presentation(daemon["last_error"], "error")
    if state == "offline":
        return Presentation(i18n.TRAY_OFFLINE, "offline", paused)
    if paused:
        return Presentation(i18n.TRAY_PAUSED, "paused", True)
    if state == "syncing":
        folder_id = daemon.get("current_folder")
        name = next((f.get("remote_name", folder_id) for f in status.get("folder_config", [])
                     if f["id"] == folder_id), folder_id) or i18n.APP_NAME
        return Presentation(i18n.TRAY_SYNCING.format(name=name), "syncing")
    enabled = [f for f in status.get("folder_config", []) if f.get("enabled", True)]
    initialized = [folders.get(f["id"], {}) for f in enabled if f.get("initialized", False)]
    errors = sum(f.get("status") in ("error", "blocked_delete") or bool(f.get("needs_resync"))
                 for f in initialized)
    if errors or state == "error":
        text = i18n.TRAY_ERRORS.format(count=errors) if errors else i18n.TRAY_ERROR
        return Presentation(text, "error")
    if not initialized:
        return Presentation(i18n.STATUSES["not_initialized"], "neutral")
    pending = next((f.get("status", "idle") for f in initialized if f.get("status") != "ok"), None)
    if pending:
        return Presentation(i18n.STATUSES.get(pending, i18n.STATUSES["idle"]), "neutral")
    waiting = sum(not f.get("initialized", False) for f in enabled)
    if waiting:
        return Presentation(i18n.TRAY_MIXED.format(current=len(initialized), waiting=waiting),
                            "neutral")
    latest = max((date for f in initialized if (date := timestamp(f.get("last_success")))),
                 default=None)
    return Presentation(i18n.TRAY_CURRENT.format(
        time=relative_time(latest.isoformat() if latest else None, now)), "ok")
