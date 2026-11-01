"""Read-only, bounded diagnostics. Backend output and credentials stay private."""

import os
from pathlib import Path
import platform
import re
import subprocess
import sys

from . import config, filters, i18n, prerequisites
from .state import StateError, StateStore, folder_state

GI_TYPES = (("GLib", "2.0"), ("Gio", "2.0"), ("GObject", "2.0"), ("Gtk", "3.0"),
            ("Gtk", "4.0"), ("Gdk", "4.0"), ("Adw", "1"), ("Notify", "0.7"),
            ("AyatanaAppIndicator3", "0.1"), ("Nautilus", "4.0"))
GI_IDS = ("gi.GLib", "gi.Gio", "gi.GObject", "gi.Gtk3", "gi.Gtk4", "gi.Gdk4", "gi.Adw", "gi.Notify",
          "gi.AyatanaAppIndicator3", "gi.Nautilus")
UNITS = ("drivesync", "drivesync-tray")
INSTALLED_MODULES = ("__init__", "app", "app_model", "cli", "client", "config", "daemon",
                     "dbus_service", "doctor", "filters", "i18n", "init_flow", "install_rclone",
                     "nautilus_ext", "nautilus_model", "notify", "paths", "prerequisites",
                     "presentation", "progress", "queue", "rclone", "setup_remote", "state",
                     "tray", "watcher")
CHECK_IDS = ("environment.os", "environment.desktop", "python.version", *GI_IDS,
             "desktop.appindicator", "rclone.version", "remote.configured", "remote.reachable",
             "install.files", "install.units",
             *(f"systemd.{unit}.{kind}" for unit in UNITS for kind in ("enabled", "active")),
             "config.valid", "inotify.watches", "state.folders")
APT_COMMAND = ("sudo apt update && sudo apt install python3-gi gir1.2-gtk-3.0 "
               "gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-notify-0.7 "
               "gir1.2-ayatanaappindicator3-0.1 python3-nautilus libnotify-bin git curl unzip")


def run(command, timeout=5):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None


def gi_available(namespace, version):
    # GTK 3 and 4 cannot be loaded in one process. Probe each typelib separately.
    code = ("import gi; gi.require_version(" + repr(namespace) + ", " + repr(version) + "); "
            "from gi.repository import " + namespace)
    result = run([sys.executable, "-c", code])
    return result is not None and result.returncode == 0


def estimate_watches(settings, cap=100000):
    count, complete = 0, True
    for folder in settings["folders"]:
        if not folder["enabled"]:
            continue
        root = Path(folder["local"]).expanduser()
        if root.is_symlink() or not root.is_dir():
            complete = False
            continue
        def failed(_):
            nonlocal complete
            complete = False
        for directory, names, _ in os.walk(root, followlinks=False, onerror=failed):
            count += 1
            if count >= cap:
                return count, False
            base = Path(directory)
            names[:] = [name for name in names if not (base / name).is_symlink()
                        and not filters.excluded((base / name).relative_to(root).as_posix(),
                                                 True, settings["excludes"])]
    return count, complete


def report(paths, *, offline=False):
    checks = []
    def add(check_id, status, fix, **details):
        checks.append(dict(id=check_id, status=status, message=i18n.DOCTOR_MESSAGES[check_id],
                           fix=fix, details=details))

    add("environment.os", "ok" if sys.platform == "linux" else "fail", "README.en.md#requirements",
        os=platform.system(), release=platform.release())
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    session = os.environ.get("XDG_SESSION_TYPE", "")
    add("environment.desktop", "ok" if desktop and session in ("x11", "wayland") else "warn",
        "README.en.md#requirements", desktop=desktop, session_type=session)
    add("python.version", "ok" if sys.version_info >= (3, 12) else "fail", "README.en.md#requirements",
        version=platform.python_version(), minimum="3.12")
    for check_id, (namespace, version) in zip(GI_IDS, GI_TYPES):
        add(check_id, "ok" if gi_available(namespace, version) else "fail", APT_COMMAND,
            namespace=namespace, version=version)
    gnome = "gnome" in desktop.lower() or "ubuntu" in desktop.lower()
    add("desktop.appindicator", "warn" if gnome else "ok", "README.en.md#requirements",
        gnome=gnome, hint=i18n.APPINDICATOR_HINT if gnome else "")

    valid = paths.config.is_file()
    try:
        settings = config.load(paths, create=False)
    except config.ConfigError:
        settings, valid = config.defaults(), False
    add("config.valid", "ok" if valid else "fail", "docs/SPEC.md",
        path=str(paths.config), exists=paths.config.exists())
    binary = prerequisites.executable(settings)
    version = run([binary, "version"])
    try:
        # Reuse the application's version boundary without a second process.
        match = re.match(r"rclone v(\d+)\.(\d+)(?:\.(\d+))?\b", version.stdout) if version else None
        recent = bool(version is not None and version.returncode == 0 and match
                      and tuple(map(int, match.group(1, 2))) >= (1, 66))
    except (TypeError, ValueError):
        match, recent = None, False
    add("rclone.version", "ok" if recent else "fail", "./install.sh --with-rclone",
        binary=binary, version=match.group(0) if match else None, minimum="1.66")
    remote_result = run([binary, "listremotes"]) if recent else None
    configured = bool(remote_result is not None and remote_result.returncode == 0
                      and settings["remote"] in remote_result.stdout.splitlines())
    setup_fix = "drivesync-cli setup-remote --client-json <path>"
    add("remote.configured", "ok" if configured else "fail", setup_fix, remote=settings["remote"])
    reachable = None
    if not offline and configured:
        reachable = run([binary, "about", settings["remote"], "--contimeout", "3s", "--timeout", "5s",
                         "--retries", "1", "--low-level-retries", "1"], timeout=8)
    add("remote.reachable", "warn" if offline else
        "ok" if reachable is not None and reachable.returncode == 0 else "fail",
        "docs/GOOGLE-CLIENT-ID.en.md", skipped=offline, timeout_seconds=8)

    home = Path.home()
    prefix = Path(os.environ.get("DRIVESYNC_PREFIX") or home / ".local").expanduser()
    unit_dir = Path(os.environ.get("DRIVESYNC_SYSTEMD_DIR") or paths.config_dir.parent / "systemd/user")
    if not prefix.is_absolute():
        prefix = home / prefix
    if not unit_dir.is_absolute():
        unit_dir = home / unit_dir
    files = [prefix / "bin" / name for name in
             ("drivesync-cli", "drivesync-daemon", "drivesync-tray", "drivesync-app")]
    files += [prefix / "lib/drivesync/drivesync" / (name + ".py") for name in INSTALLED_MODULES]
    files.append(prefix / "lib/drivesync/drivesync/style.css")
    files += [paths.data_dir.parent / "applications/io.github.kocakburhan.DriveSync.desktop",
              paths.data_dir.parent / "nautilus-python/extensions/drivesync.py",
              paths.data_dir.parent / "icons/hicolor/scalable/apps/io.github.kocakburhan.DriveSync.svg"]
    missing = [str(path) for path in files if not path.is_file()]
    add("install.files", "fail" if missing else "ok", "./install.sh", missing=missing)
    missing = [str(unit_dir / (unit + ".service")) for unit in UNITS
               if not (unit_dir / (unit + ".service")).is_file()]
    add("install.units", "fail" if missing else "ok", "./install.sh", missing=missing)
    for unit in UNITS:
        for kind in ("enabled", "active"):
            result = run(["systemctl", "--user", "is-" + kind, unit + ".service"])
            expected = "enabled" if kind == "enabled" else "active"
            ok = result is not None and result.returncode == 0 and result.stdout.strip() == expected
            add(f"systemd.{unit}.{kind}", "ok" if ok else "fail",
                f"systemctl --user enable --now {unit}.service")
    estimated, complete = estimate_watches(settings)
    try:
        limit = int(Path("/proc/sys/fs/inotify/max_user_watches").read_text().strip())
    except (OSError, ValueError):
        limit = None
    add("inotify.watches", "ok" if complete and limit is not None and estimated <= min(
        limit, settings["max_monitors"]) else "warn", "docs/WATCHERS.md",
        max_user_watches=limit, estimated_need=estimated, estimate_complete=complete,
        max_monitors=settings["max_monitors"])
    try:
        state = StateStore(paths).read()
        folders = []
        for folder in settings["folders"]:
            item = state["folders"].get(folder["id"], folder_state())
            folders.append(dict(id=folder["id"], enabled=folder["enabled"], initialized=folder["initialized"],
                                status=item["status"], needs_resync=item["needs_resync"],
                                last_success=item["last_success"], progress=item["progress"]))
        problem = not folders or any(f["enabled"] and (not f["initialized"] or f["needs_resync"]
                                  or f["status"] in ("error", "blocked_delete", "offline")) for f in folders)
        failed = any(f["enabled"] and (f["needs_resync"] or f["status"] in ("error", "blocked_delete"))
                     for f in folders)
        add("state.folders", "fail" if failed else "warn" if problem else "ok",
            "drivesync-cli status --json", folders=folders)
    except StateError:
        add("state.folders", "fail", "docs/SPEC.md", folders=[], readable=False)
    return dict(schema_version=1, ok=not any(c["status"] == "fail" for c in checks), checks=checks)
