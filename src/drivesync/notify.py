"""Optional libnotify adapter with per-folder error suppression and action buttons."""

import logging
import shutil
import subprocess

from . import i18n

APP_ID = "io.github.kocakburhan.DriveSync"
# Notifications stay referenced until "closed"; this bound only protects against a server that never closes them.
MAX_LIVE_NOTIFICATIONS = 16
# Action id -> translation constant for its button label.
ACTION_LABELS = {"review": "NOTIFY_REVIEW", "conflicts": "NOTIFY_SHOW_CONFLICTS"}


def open_window():
    """Start the main window; a running instance is raised by the application itself."""
    for command in (["drivesync-app"], ["gtk-launch", APP_ID]):
        executable = shutil.which(command[0])
        if executable:
            subprocess.Popen([executable, *command[1:]], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return True
    logging.getLogger(__name__).debug("No launcher found for the DriveSync window")
    return False


class Notifier:
    def __init__(self, send=None, actions=True, launch=None):
        self.errors = set()
        self.offline_sent = False
        self.send = send or self._desktop
        self.notify = None
        # Buttons only work while a GLib main loop runs (the daemon); the CLI exits first.
        self.actions = actions
        self.launch = launch or open_window
        self.actions_supported = None
        self._live = {}
        self._next_key = 0

    def _desktop(self, title, body, action=None):
        # libnotify probes portals during init; defer that until an actual notification.
        if self.notify is None:
            try:
                import gi
                gi.require_version("Notify", "0.7")
                from gi.repository import Notify
                Notify.init(i18n.APP_NAME)
                self.notify = Notify
            except (ImportError, ValueError):
                pass

        if self.notify:
            try:
                notification = self.notify.Notification.new(title, body, "dialog-warning-symbolic")
                if action and self.actions and self._actions_supported():
                    self._add_action(notification, action)
                notification.show()
            except Exception:
                logging.getLogger(__name__).debug("Notification unavailable")

    def _actions_supported(self):
        if self.actions_supported is None:
            try:
                self.actions_supported = "actions" in self.notify.get_server_caps()
            except Exception:
                self.actions_supported = False
        return self.actions_supported

    def _add_action(self, notification, action):
        label = getattr(i18n, ACTION_LABELS[action])
        notification.add_action(action, label, self._on_action, None)
        key = self._next_key
        self._next_key += 1
        self._live[key] = notification
        while len(self._live) > MAX_LIVE_NOTIFICATIONS:
            self._live.pop(next(iter(self._live)))
        notification.connect("closed", self._forget, key)

    def _on_action(self, _notification, _action, _user_data):
        try:
            self.launch()
        except Exception:
            logging.getLogger(__name__).debug("Could not open the DriveSync window", exc_info=True)

    def _forget(self, _notification, key):
        self._live.pop(key, None)

    def folder(self, folder_id, name, item):
        if item["status"] in ("error", "blocked_delete") or item["needs_resync"]:
            if folder_id not in self.errors:
                self.errors.add(folder_id)
                self.send(i18n.NOTIFY_ERROR.format(name=name), item.get("last_error") or i18n.SYNC_ERROR,
                          "review")
        elif item["status"] == "ok":
            self.errors.discard(folder_id)
            if item.get("conflicts", 0):
                self.send(i18n.APP_NAME, i18n.CONFLICTS.format(count=item["conflicts"]), "conflicts")

    def offline(self, duration):
        if duration is None:
            self.offline_sent = False
        elif duration >= 1800 and not self.offline_sent:
            self.offline_sent = True
            self.send(i18n.APP_NAME, i18n.OFFLINE)

    def init_needs_approval(self, name):
        self.send(i18n.APP_NAME, i18n.INIT_NEEDS_APPROVAL_NOTIFY.format(name=name), "review")

    def init_failed(self, name, message):
        self.send(i18n.NOTIFY_ERROR.format(name=name), message, "review")
