"""Optional libnotify adapter with per-folder error suppression."""

import logging

from . import i18n


class Notifier:
    def __init__(self, send=None):
        self.errors = set()
        self.offline_sent = False
        self.send = send or self._desktop
        self.send_update = None if send else self._desktop_update
        self.notify = None
        self.pending = []

    def _libnotify(self):
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
        return self.notify

    def _desktop(self, title, body):
        if self._libnotify():
            try:
                self.notify.Notification.new(title, body, "dialog-warning-symbolic").show()
            except Exception:
                logging.getLogger(__name__).debug("Notification unavailable")

    def _desktop_update(self, title, body, label, callback):
        """Notification with an action button when the server supports actions; else plain text."""
        if not self._libnotify():
            return
        try:
            note = self.notify.Notification.new(title, body, "software-update-available-symbolic")
            if "actions" in (self.notify.get_server_caps() or []):
                note.add_action("update", label, lambda *_: callback())
            # Keep a reference: libnotify delivers the action only while the object lives.
            self.pending.append(note)
            note.connect("closed", lambda closed: self.pending.remove(closed) if closed in self.pending else None)
            note.show()
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

    def init_needs_approval(self, name):
        self.send(i18n.APP_NAME, i18n.INIT_NEEDS_APPROVAL_NOTIFY.format(name=name))

    def init_failed(self, name, message):
        self.send(i18n.NOTIFY_ERROR.format(name=name), message)

    def update_available(self, version, url, on_update):
        """Announce a new release once; the action (when supported) runs ``on_update``."""
        title = i18n.UPDATE_TITLE.format(version=version)
        body = i18n.UPDATE_BODY.format(url=url)
        if self.send_update is None:
            # Injected senders (tests, GUI-less callers) get plain text only.
            self.send(title, body)
        else:
            self.send_update(title, body, i18n.UPDATE_ACTION, on_update)
