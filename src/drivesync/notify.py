"""Optional libnotify adapter with per-folder error suppression."""

import logging

from . import i18n


class Notifier:
    def __init__(self, send=None):
        self.errors = set()
        self.offline_sent = False
        self.send = send or self._desktop
        self.notify = None

    def _desktop(self, title, body):
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
                self.notify.Notification.new(title, body, "dialog-warning-symbolic").show()
            except Exception:
                logging.getLogger(__name__).debug("Notification unavailable")

    def folder(self, folder_id, name, item):
        if item["status"] in ("error", "blocked_delete") or item["needs_resync"]:
            if folder_id not in self.errors:
                self.errors.add(folder_id)
                self.send(i18n.NOTIFY_ERROR.format(name=name), item.get("last_error") or i18n.SYNC_ERROR)
        elif item["status"] == "ok":
            self.errors.discard(folder_id)
            if item.get("conflicts", 0):
                self.send(i18n.APP_NAME, i18n.CONFLICTS.format(count=item["conflicts"]))

    def offline(self, duration):
        if duration is None:
            self.offline_sent = False
        elif duration >= 1800 and not self.offline_sent:
            self.offline_sent = True
            self.send(i18n.APP_NAME, i18n.OFFLINE)
