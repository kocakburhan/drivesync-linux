"""Shared asynchronous daemon client for GTK 3 and GTK 4 frontends."""

import json
import sys

from gi.repository import Gio, GLib

from . import dbus_service, i18n

DRIVE_URL = "https://drive.google.com/drive/my-drive"


class StatusClient:
    def start_client(self):
        self.status = None
        self.connection = self.owner = self.subscription = None
        self.status_revision = 0
        self.watch = Gio.bus_watch_name(Gio.BusType.SESSION, dbus_service.BUS_NAME,
                                       Gio.BusNameWatcherFlags.NONE, self._appeared, self._vanished)

    def close_client(self):
        Gio.bus_unwatch_name(self.watch)
        self._unsubscribe()
        self.owner = None

    def request_failed(self, message):
        print(message, file=sys.stderr)

    def request_finished(self, method, reply):
        pass

    def _unsubscribe(self):
        if self.connection is not None and self.subscription is not None:
            self.connection.signal_unsubscribe(self.subscription)
        self.subscription = None

    def _appeared(self, connection, name, owner):
        self._unsubscribe()
        self.connection, self.owner = connection, owner
        self.subscription = connection.signal_subscribe(
            owner, dbus_service.INTERFACE, "StatusChanged", dbus_service.OBJECT_PATH,
            None, Gio.DBusSignalFlags.NONE, self._changed)
        self._request("GetStatus")

    def _vanished(self, *_):
        self._unsubscribe()
        self.connection = self.owner = self.status = None
        self.refresh()

    def _accept(self, payload):
        try:
            value = json.loads(payload)
            if not isinstance(value, dict):
                return
        except (ValueError, TypeError):
            return
        self.status = value
        self.refresh()

    def _changed(self, connection, sender, path, interface, signal, parameters):
        if sender != self.owner:
            return
        self.status_revision += 1
        self._accept(parameters.unpack()[0])

    def _request(self, method, parameters=None):
        if self.connection is None or self.owner is None:
            return
        owner = self.owner
        revision = self.status_revision

        def finished(connection, result):
            try:
                reply = connection.call_finish(result).unpack()[0]
                if self.owner == owner and method == "GetStatus":
                    # A newer signal can arrive before the initial request completes.
                    if self.status_revision == revision:
                        self._accept(reply)
                elif self.owner == owner and reply is False:
                    self.request_failed(i18n.REQUEST_REFUSED)
                if self.owner == owner:
                    self.request_finished(method, reply)
            except GLib.Error:
                self.request_failed(i18n.NO_DAEMON)

        self.connection.call(owner, dbus_service.OBJECT_PATH, dbus_service.INTERFACE,
                             method, parameters, None, Gio.DBusCallFlags.NONE, 5000, None, finished)

