import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from unittest.mock import Mock

from gi.repository import GLib

from support import IsolatedTest, ROOT
from drivesync.dbus_service import INTERFACE, Service, XML


class DBusLauncherTests(IsolatedTest):
    def test_introspection_signatures_match_contract_exactly(self):
        interface = ET.fromstring(XML).find("interface")
        self.assertEqual(interface.attrib["name"], INTERFACE)
        expected = {"GetStatus": [("s", "out")], "SyncNow": [("s", "in"), ("b", "out")],
                    "StartInit": [("s", "in"), ("s", "in"), ("b", "out")],
                    "Pause": [("b", "out")], "Resume": [("b", "out")], "ReloadConfig": [("b", "out")]}
        actual = {method.attrib["name"]: [(arg.attrib["type"], arg.attrib["direction"])
                                         for arg in method] for method in interface.findall("method")}
        self.assertEqual(actual, expected)
        signals = interface.findall("signal")
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].attrib["name"], "StatusChanged")
        self.assertEqual([arg.attrib["type"] for arg in signals[0]], ["s"])

    def service(self):
        service = Service.__new__(Service)
        service.controller = Mock()
        service.controller.status_json.return_value = '{"paused":false}'
        service.connection = Mock()
        service.connection.is_closed.return_value = False
        service.last_signal = 0
        service.pending_signal = None
        self.addCleanup(lambda: GLib.source_remove(service.pending_signal)
                        if service.pending_signal is not None else None)
        return service

    def test_method_dispatch_and_wire_values(self):
        service = self.service()
        for method, parameters, callback, arguments in (
                ("SyncNow", GLib.Variant("(s)", ("docs",)), "sync_now", ("docs",)),
                ("Pause", GLib.Variant("()", ()), "pause", (True,)),
                ("Resume", GLib.Variant("()", ()), "pause", (False,)),
                ("ReloadConfig", GLib.Variant("()", ()), "reload_config", ())):
            with self.subTest(method=method):
                invocation = Mock()
                getattr(service.controller, callback).return_value = True
                service._method(None, None, None, INTERFACE, method, parameters, invocation)
                getattr(service.controller, callback).assert_called_with(*arguments)
                reply = invocation.return_value.call_args.args[0]
                self.assertEqual(reply.get_type_string(), "(b)")
                self.assertEqual(reply.unpack(), (True,))
        invocation = Mock()
        service._method(None, None, None, INTERFACE, "GetStatus", None, invocation)
        reply = invocation.return_value.call_args.args[0]
        self.assertEqual(reply.get_type_string(), "(s)")
        self.assertEqual(json.loads(reply.unpack()[0]), {"paused": False})

    def test_status_signal_is_coalesced_and_rate_limited(self):
        service = self.service()
        for _ in range(20):
            service.changed()
        source_id = service.pending_signal
        self.assertIsNotNone(source_id)
        context = GLib.MainContext.default()
        deadline = time.monotonic() + 0.05
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            time.sleep(0.001)
        self.assertEqual(service.connection.emit_signal.call_count, 1)
        service.changed()
        deadline = time.monotonic() + 0.1
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            time.sleep(0.001)
        self.assertEqual(service.connection.emit_signal.call_count, 1)

    def test_cli_launcher_works_from_checkout(self):
        result = subprocess.run([sys.executable, str(ROOT / "bin/drivesync-cli"), "--help"],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("force-sync", result.stdout)

    def test_cli_launcher_works_in_both_installed_layouts(self):
        for nested in (False, True):
            with self.subTest(nested=nested):
                prefix = self.root / str(nested)
                binary = prefix / "bin/drivesync-cli"
                binary.parent.mkdir(parents=True)
                shutil.copy(ROOT / "bin/drivesync-cli", binary)
                library = prefix / "lib/drivesync" if nested else prefix / "lib"
                shutil.copytree(ROOT / "src/drivesync", library / "drivesync",
                                ignore=shutil.ignore_patterns("__pycache__"))
                result = subprocess.run([sys.executable, str(binary), "--help"],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_gdbus_boolean_and_json_output_parsing(self):
        self.assertIs(GLib.Variant.parse(None, "(true,)", None, None).unpack()[0], True)
        result = GLib.Variant.parse(None, "('{\"paused\":false}',)", None, None).unpack()[0]
        self.assertEqual(json.loads(result), {"paused": False})
