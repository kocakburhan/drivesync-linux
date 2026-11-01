#!/usr/bin/env python3
"""Regenerate docs/screenshots/*.png from DEMO data only.

Usage: python3 tools/make_screenshots.py [OUTPUT_DIR]

The script re-executes itself inside `dbus-run-session` with throw-away XDG
directories, so it never talks to a real daemon or touches real settings or
Google Drive. Windows are rendered with GTK's own renderer (no desktop
capture), so nothing outside the DriveSync window can appear in the images.
Needs a display (X11/Wayland session) for GTK to initialise.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def reexec():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "docs/screenshots").resolve()
    out.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix="drivesync-shots-"))
    try:
        env = dict(os.environ, HOME=str(temp / "home"), XDG_CONFIG_HOME=str(temp / "cfg"),
                   XDG_STATE_HOME=str(temp / "state"), XDG_DATA_HOME=str(temp / "data"),
                   DRIVESYNC_SHOTS_CHILD="1", DRIVESYNC_SHOTS_OUT=str(out))
        for name in ("home", "cfg", "state", "data"):
            (temp / name).mkdir()
        env.pop("DBUS_SESSION_BUS_ADDRESS", None)
        return subprocess.call(["dbus-run-session", "--", sys.executable, __file__], env=env)
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def demo_status(home):
    now = datetime.now(timezone.utc)
    ago = lambda **kw: (now - timedelta(**kw)).isoformat()
    names = [("belgeler", "Belgeler"), ("masaustu", "Masaüstü"),
             ("projeler", "Projeler"), ("resimler", "Resimler")]
    folders = []
    for folder_id, name in names:
        (home / name).mkdir(exist_ok=True)
        folders.append(dict(id=folder_id, local=str(home / name), remote_name=name,
                            enabled=True, initialized=folder_id != "resimler"))
    ok = lambda minutes: dict(status="ok", last_success=ago(minutes=minutes), conflicts=0)
    state = {
        "belgeler": ok(3),
        "masaustu": ok(3),
        "projeler": dict(status="syncing", last_success=ago(hours=1), progress=dict(
            bytes_done=round(5.8 * 1024 ** 3), bytes_total=round(9.2 * 1024 ** 3), percent=63.0,
            speed=1.2 * 1024 ** 2, eta_seconds=48 * 60, files_done=3386, files_total=3604)),
        "resimler": dict(status="not_initialized"),
    }
    status = dict(daemon=dict(status="syncing", current_folder="projeler"), paused=False,
                  folder_config=folders, folders=state, recent_conflicts=[])
    return folders, status


def save_png(widget, path):
    from gi.repository import Gtk
    paintable = Gtk.WidgetPaintable.new(widget)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, widget.get_width(), widget.get_height())
    node = snapshot.to_node()
    texture = widget.get_native().get_renderer().render_texture(node, None)
    texture.save_to_png(str(path))


def child():
    sys.path.insert(0, str(ROOT / "src"))
    from unittest.mock import patch
    from drivesync import app, config
    from drivesync.init_flow import Plan
    from gi.repository import GLib

    out = Path(os.environ["DRIVESYNC_SHOTS_OUT"])
    home = Path(os.environ["HOME"])
    folders, status = demo_status(home)
    application = app.DriveSyncApp()
    application.set_flags(app.Gio.ApplicationFlags.NON_UNIQUE)

    def pump(seconds):
        loop = GLib.MainLoop()
        GLib.timeout_add(int(seconds * 1000), loop.quit)
        loop.run()

    with patch.object(app.Gio, "bus_watch_name", return_value=1), \
            patch.object(app.Gio, "bus_unwatch_name"):
        application.register(None)
        config.save(application.paths, dict(config.defaults(), rclone_path="/bin/true", folders=folders))
        application.activate()
        application.window.set_default_size(900, 700)
        application._accept(json.dumps(status))
        application.settings.set_property("gtk-enable-animations", False)
        application.refresh()
        pump(1.5)
        save_png(application.window, out / "window.png")

        plan = Plan(212, 14, 2, 0, (
            "Drive'a kopyalanacak: 2024/IMG_0001.jpg", "Drive'a kopyalanacak: 2024/IMG_0002.jpg",
            "Drive'a kopyalanacak: Tatil/plaj.jpg", "Bilgisayara kopyalanacak: Eski/anilar.jpg",
            "Silinecek: kopya/IMG_0001 (kopya).jpg", "Silinecek: kopya/IMG_0002 (kopya).jpg"))
        dialog, typed = application.show_init_summary(folders[3], plan)
        dialog.set_default_size(560, -1)
        typed.set_text("Resimler")
        expander = dialog.get_extra_child().get_first_child()
        expander.set_expanded(True)
        pump(1.0)
        save_png(dialog, out / "initial-sync.png")
        dialog.close()
        pump(0.3)

        application.preferences()
        pump(1.0)
        settings = next(w for w in app.Gtk.Window.list_toplevels() if isinstance(w, app.Adw.PreferencesWindow))
        save_png(settings, out / "settings.png")
        settings.close()
        pump(0.3)
        application.window.close()
        pump(0.3)
        application.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(child() if os.environ.get("DRIVESYNC_SHOTS_CHILD") else reexec())
