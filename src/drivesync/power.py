"""Metered-network and battery detection for automatic pausing.

Both sources are best effort: when NetworkManager/UPower are missing or unreachable the
condition is simply treated as inactive, so synchronization is never blocked by a probe failure.
The daemon only needs the small interface below, which tests replace with a fake:
``metered``, ``on_battery``, ``callback`` (called without arguments on any change), ``close()``.
"""

from gi.repository import Gio, GLib

UPOWER = ("org.freedesktop.UPower", "/org/freedesktop/UPower", "org.freedesktop.UPower")


class NullPower:
    """Never active; the default when no real monitor is injected."""
    metered = False
    on_battery = False
    callback = None

    def close(self):
        pass


class SystemPower:
    def __init__(self, callback=None, network_monitor=None):
        self.callback = callback
        self.on_battery = False
        self.proxy = None
        self.closed = False
        self.network = network_monitor
        self.handlers = []
        try:
            self.network = network_monitor or Gio.NetworkMonitor.get_default()
            self.handlers.append((self.network, self.network.connect(
                "notify::network-metered", self._changed)))
        except (GLib.Error, TypeError, AttributeError):
            self.network = None
        # Asynchronous: the system bus must never delay daemon start-up.
        try:
            Gio.DBusProxy.new_for_bus(Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None, *UPOWER,
                                      None, self._proxy_ready)
        except GLib.Error:
            pass

    @property
    def metered(self):
        try:
            return bool(self.network is not None and self.network.get_network_metered())
        except (GLib.Error, AttributeError):
            return False

    def _proxy_ready(self, source, result):
        try:
            proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error:
            return  # No UPower: treat as not on battery.
        if self.closed:
            return
        self.proxy = proxy
        self.handlers.append((proxy, proxy.connect("g-properties-changed", self._upower_changed)))
        self.handlers.append((proxy, proxy.connect("notify::g-name-owner", self._upower_changed)))
        self._upower_changed()

    def _upower_changed(self, *_):
        value = None
        if self.proxy is not None and self.proxy.get_name_owner():
            value = self.proxy.get_cached_property("OnBattery")
        battery = bool(value.unpack()) if value is not None else False
        if battery != self.on_battery:
            self.on_battery = battery
            self._changed()

    def _changed(self, *_):
        if self.callback and not self.closed:
            self.callback()

    def close(self):
        self.closed = True
        for obj, handler in self.handlers:
            obj.disconnect(handler)
        self.handlers = []
