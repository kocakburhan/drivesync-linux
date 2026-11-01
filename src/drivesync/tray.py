"""GTK 3 tray client. The daemon owns all synchronization and configuration."""

from pathlib import Path
import shutil
import os
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3, Gio, GLib, Gtk

from . import dbus_service, i18n
from .client import DRIVE_URL, StatusClient
from .paths import Paths
from .presentation import TRAY_ICONS, TRAY_FRAMES, present, tray_frame

def app_launcher():
    sibling = Path(sys.argv[0]).resolve().with_name("drivesync-app")
    return shutil.which("drivesync-app") or (str(sibling) if sibling.is_file() and os.access(sibling, os.X_OK) else None)


def build_menu(view, activate, app_path=None):
    """Build an unshown menu, also usable by the display-gated smoke test."""
    menu = Gtk.Menu()

    def item(label, action=None, enabled=True):
        widget = Gtk.MenuItem.new_with_label(label)
        widget.set_sensitive(enabled)
        if action:
            widget.connect("activate", lambda _: activate(action))
        menu.append(widget)

    item(view.text, enabled=False)
    if not view.available:
        item(i18n.START_SERVICE, "start")
    item(i18n.SYNC_NOW, "sync", view.available)
    item(i18n.RESUME if view.paused else i18n.PAUSE, "resume" if view.paused else "pause",
         view.available)
    item(i18n.OPEN_LOGS, "logs")
    item(i18n.OPEN_DRIVE, "drive")
    menu.append(Gtk.SeparatorMenuItem())
    item(i18n.OPEN_WINDOW, "window", bool(app_path))
    return menu


class Tray(StatusClient):
    def __init__(self):
        self.paths = Paths.from_env()
        self.status = None
        self.connection = None
        self.owner = None
        self.subscription = None
        self.status_revision = 0
        self.menu = None
        self.frame = 0
        self.animation_timer = None
        self.animating = False
        self.settings = Gtk.Settings.get_default()
        self.settings_handler = self.settings.connect("notify::gtk-enable-animations", lambda *_: self.refresh())
        self.app_path = app_launcher()
        self.indicator = AyatanaAppIndicator3.Indicator.new(
            "drivesync", TRAY_ICONS["ok"][0], AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS)
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        self.refresh()
        self.timer = GLib.timeout_add_seconds(60, self.refresh)
        self.start_client()

    @staticmethod
    def resolve_icon(candidates):
        theme = Gtk.IconTheme.get_default()
        return next((name for name in candidates if Path(name).is_file() or theme.has_icon(name)),
                    candidates[-1])

    def refresh(self):
        view = present(self.status)
        self.indicator.set_icon_full(self.resolve_icon(TRAY_ICONS[view.icon]), view.text)
        self.icon_description = view.text
        self.animating = view.icon == "syncing" and self.settings.get_property("gtk-enable-animations")
        if self.animating and self.animation_timer is None:
            self.frame = 0
            self.indicator.set_icon_full(self.resolve_icon(TRAY_FRAMES[0]), view.text)
            self.animation_timer = GLib.timeout_add(125, self.animate)
        elif not self.animating and self.animation_timer is not None:
            GLib.source_remove(self.animation_timer)
            self.animation_timer = None
        elif self.animating:
            self.indicator.set_icon_full(self.resolve_icon(TRAY_FRAMES[self.frame]), view.text)
        previous = self.menu
        self.menu = build_menu(view, self.activate, self.app_path)
        self.menu.show_all()
        self.indicator.set_menu(self.menu)
        if previous is not None:
            previous.destroy()
        return GLib.SOURCE_CONTINUE

    def animate(self):
        frame = tray_frame(self.frame, self.animating,
                           self.settings.get_property("gtk-enable-animations"))
        if frame is None:
            self.animation_timer = None
            return GLib.SOURCE_REMOVE
        self.frame = frame
        self.indicator.set_icon_full(self.resolve_icon(TRAY_FRAMES[frame]), self.icon_description)
        return GLib.SOURCE_CONTINUE

    def activate(self, action):
        try:
            if action == "sync":
                self._request("SyncNow", GLib.Variant("(s)", ("",)))
            elif action in ("pause", "resume"):
                self._request("Pause" if action == "pause" else "Resume")
            elif action == "start":
                self._spawn(["systemctl", "--user", "start", "drivesync.service"])
            elif action == "logs":
                self.paths.logs.mkdir(parents=True, exist_ok=True, mode=0o700)
                Gio.AppInfo.launch_default_for_uri(self.paths.logs.as_uri(), None)
            elif action == "drive":
                Gio.AppInfo.launch_default_for_uri(DRIVE_URL, None)
            elif action == "window" and self.app_path:
                self._spawn([self.app_path])
        except (GLib.Error, OSError):
            print(i18n.REQUEST_REFUSED, file=sys.stderr)

    @staticmethod
    def _spawn(argv):
        process = Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE)

        def finished(child, result):
            try:
                if not child.wait_check_finish(result):
                    print(i18n.REQUEST_REFUSED, file=sys.stderr)
            except GLib.Error:
                print(i18n.REQUEST_REFUSED, file=sys.stderr)

        process.wait_check_async(None, finished)

    def close(self):
        self.close_client()
        GLib.source_remove(self.timer)
        if self.animation_timer is not None:
            GLib.source_remove(self.animation_timer)
        self.settings.disconnect(self.settings_handler)
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.PASSIVE)
        self.menu.destroy()


def main():
    if not Gtk.init_check()[0]:
        print(i18n.DAEMON_FAILED, file=sys.stderr)
        return 1
    tray = Tray()
    try:
        Gtk.main()
    finally:
        tray.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
