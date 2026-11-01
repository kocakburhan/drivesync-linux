"""Stable session-bus interface shared with the future tray."""

from gi.repository import Gio, GLib

from . import i18n

BUS_NAME = "io.github.kocakburhan.DriveSync"
OBJECT_PATH = "/io/github/kocakburhan/DriveSync"
INTERFACE = "io.github.kocakburhan.DriveSync1"
XML = f"""<node><interface name="{INTERFACE}">
  <method name="GetStatus"><arg type="s" direction="out"/></method>
  <method name="SyncNow"><arg type="s" direction="in"/><arg type="b" direction="out"/></method>
  <method name="StartInit"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="b" direction="out"/></method>
  <method name="Pause"><arg type="b" direction="out"/></method>
  <method name="Resume"><arg type="b" direction="out"/></method>
  <method name="ReloadConfig"><arg type="b" direction="out"/></method>
  <signal name="StatusChanged"><arg type="s"/></signal>
</interface></node>"""


class Service:
    def __init__(self, controller):
        self.controller = controller
        self.pending_signal = None
        self.last_signal = 0
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = self.connection.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "RequestName", GLib.Variant("(su)", (BUS_NAME, 4)), GLib.VariantType.new("(u)"),
            Gio.DBusCallFlags.NONE, 3000, None)
        if reply.unpack()[0] != 1:
            raise RuntimeError(i18n.ALREADY_RUNNING)
        info = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
        self.registration = self.connection.register_object(OBJECT_PATH, info, self._method, None, None)
        self.connection.set_exit_on_close(False)
        self.closed_handler = self.connection.connect("closed", self._closed)

    def _closed(self, *_):
        self.controller.shutdown()

    def _method(self, connection, sender, path, interface, method, parameters, invocation):
        try:
            if method == "GetStatus":
                reply = GLib.Variant("(s)", (self.controller.status_json(),))
            else:
                callbacks = {"SyncNow": lambda: self.controller.sync_now(parameters.unpack()[0]),
                             "StartInit": lambda: self.controller.start_init(*parameters.unpack()),
                             "Pause": lambda: self.controller.pause(True),
                             "Resume": lambda: self.controller.pause(False),
                             "ReloadConfig": self.controller.reload_config}
                reply = GLib.Variant("(b)", (bool(callbacks[method]()),))
            invocation.return_value(reply)
        except Exception:
            invocation.return_dbus_error(INTERFACE + ".Error", i18n.REQUEST_REFUSED)

    def changed(self):
        if self.pending_signal is None:
            elapsed = GLib.get_monotonic_time() - self.last_signal
            delay = max(1, int((500000 - elapsed) / 1000) + 1)
            self.pending_signal = GLib.timeout_add(delay, self._emit)

    def _emit(self):
        self.pending_signal = None
        self.last_signal = GLib.get_monotonic_time()
        if not self.connection.is_closed():
            self.connection.emit_signal(None, OBJECT_PATH, INTERFACE, "StatusChanged",
                                        GLib.Variant("(s)", (self.controller.status_json(),)))
        return GLib.SOURCE_REMOVE

    def close(self):
        if self.pending_signal is not None:
            GLib.source_remove(self.pending_signal)
            self.pending_signal = None
        self.connection.disconnect(self.closed_handler)
        self.connection.unregister_object(self.registration)
        if not self.connection.is_closed():
            self.connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                      "org.freedesktop.DBus", "ReleaseName",
                                      GLib.Variant("(s)", (BUS_NAME,)), None,
                                      Gio.DBusCallFlags.NONE, 3000, None)


def call(method, *arguments):
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    parameters = GLib.Variant("(" + "s" * len(arguments) + ")", arguments) if arguments else None
    result = connection.call_sync(BUS_NAME, OBJECT_PATH, INTERFACE, method, parameters,
                                  None, Gio.DBusCallFlags.NONE, 5000, None)
    return result.unpack()[0]


def daemon_running():
    """True when the daemon owns the bus name; False when no session bus or owner."""
    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        result = connection.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "NameHasOwner", GLib.Variant("(s)", (BUS_NAME,)), GLib.VariantType.new("(b)"),
            Gio.DBusCallFlags.NONE, 3000, None)
        return bool(result.unpack()[0])
    except GLib.Error:
        return False
