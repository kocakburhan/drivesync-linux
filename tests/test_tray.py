import os
import unittest

import support


@unittest.skipUnless(os.environ.get("DISPLAY"), "DISPLAY yok; GTK menü testi atlandı")
class TraySmokeTests(unittest.TestCase):
    def test_import_and_unshown_menu(self):
        from drivesync import tray
        from drivesync.presentation import present
        if not tray.Gtk.init_check()[0]:
            self.skipTest("GTK görüntüye bağlanamadı")
        actions = []
        for status in (None, {"paused": True}, {"daemon": {"status": "offline"}}):
            with self.subTest(status=status):
                menu = tray.build_menu(present(status), actions.append)
                self.addCleanup(menu.destroy)
                children = menu.get_children()
                labels = [child.get_label() for child in children
                          if not isinstance(child, tray.Gtk.SeparatorMenuItem)]
                self.assertIn("Şimdi eşitle", labels)
                self.assertIn("Kayıtları aç", labels)
                self.assertIn("Drive'da aç", labels)
                self.assertEqual(labels[-1], "Pencereyi aç")
                self.assertFalse(children[-1].get_sensitive())
                self.assertFalse(children[0].get_sensitive())
                self.assertFalse(menu.get_visible())
                if status is None:
                    self.assertIn("Servisi başlat", labels)
                    self.assertFalse(children[2].get_sensitive())
                    children[1].activate()
                    self.assertEqual(actions[-1], "start")
                elif status.get("paused"):
                    self.assertIn("Devam et", labels)
                    children[2].activate()
                    self.assertEqual(actions[-1], "resume")
                else:
                    self.assertEqual(labels[0], "Çevrimdışı")


class TrayClientTests(unittest.TestCase):
    """Exercise the bus lifecycle without creating GTK widgets or a real bus."""

    def setUp(self):
        from unittest.mock import Mock
        from drivesync import tray
        self.module = tray
        self.client = tray.Tray.__new__(tray.Tray)
        self.client.connection = None
        self.client.owner = None
        self.client.subscription = None
        self.client.status = None
        self.client.status_revision = 0
        self.client.refresh = Mock()
        self.connection = Mock()
        self.connection.signal_subscribe.return_value = 42

    def appear(self, owner=":1.2"):
        self.client._appeared(self.connection, self.module.dbus_service.BUS_NAME, owner)
        return self.connection.call.call_args.args[-1]

    def test_appearance_signal_disappearance_and_reappearance(self):
        from unittest.mock import Mock
        finished = self.appear()
        args = self.connection.signal_subscribe.call_args.args
        self.assertEqual(args[:4], (":1.2", self.module.dbus_service.INTERFACE, "StatusChanged",
                                    self.module.dbus_service.OBJECT_PATH))
        self.assertEqual(self.connection.call.call_args.args[3], "GetStatus")
        self.connection.call_finish.return_value = self.module.GLib.Variant(
            "(s)", ('{"paused":true}',))
        finished(self.connection, Mock())
        self.assertEqual(self.client.status, {"paused": True})
        self.client._changed(self.connection, ":1.2", None, None, "StatusChanged",
                             self.module.GLib.Variant("(s)", ('{"daemon":{"status":"offline"}}',)))
        self.assertEqual(self.client.status["daemon"]["status"], "offline")
        self.client._vanished()
        self.connection.signal_unsubscribe.assert_called_once_with(42)
        self.assertIsNone(self.client.status)
        self.assertIsNone(self.client.owner)
        self.appear(":1.3")
        self.assertEqual(self.client.owner, ":1.3")
        # The previous owner's delayed reply must not restore its stale status.
        finished(self.connection, Mock())
        self.assertIsNone(self.client.status)

    def test_signal_wins_over_initial_reply(self):
        from unittest.mock import Mock
        finished = self.appear()
        self.client._changed(self.connection, ":1.2", None, None, "StatusChanged",
                             self.module.GLib.Variant("(s)", ('{"paused":true}',)))
        self.connection.call_finish.return_value = self.module.GLib.Variant(
            "(s)", ('{"paused":false}',))
        finished(self.connection, Mock())
        self.assertEqual(self.client.status, {"paused": True})

    def test_menu_actions_use_existing_bus_contract(self):
        self.appear()
        for action, method in (("sync", "SyncNow"), ("pause", "Pause"), ("resume", "Resume")):
            with self.subTest(action=action):
                self.client.activate(action)
                args = self.connection.call.call_args.args
                self.assertEqual(args[3], method)
                if action == "sync":
                    self.assertEqual(args[4].unpack(), ("",))
                else:
                    self.assertIsNone(args[4])

    def test_malformed_payload_does_not_replace_status(self):
        self.client.status = {"paused": True}
        for value in ("garbage", "[]", "null"):
            self.client._accept(value)
            self.assertEqual(self.client.status, {"paused": True})

    def test_window_action_launches_installed_app_without_shell(self):
        from unittest.mock import Mock
        self.client.app_path = "/temporary/bin/drivesync-app"
        self.client._spawn = Mock()
        self.client.activate("window")
        self.client._spawn.assert_called_once_with([self.client.app_path])

    def test_animation_timer_exists_only_when_syncing_with_motion_enabled(self):
        from unittest.mock import Mock, patch
        client = self.client
        client.indicator = Mock()
        client.settings = Mock()
        client.settings.get_property.return_value = True
        client.frame = 0
        client.animation_timer = None
        client.menu = None
        client.app_path = None
        client.status = dict(folders=dict(docs=dict(status="syncing")))
        with patch.object(self.module.Gtk.IconTheme, "get_default", return_value=Mock()), \
                patch.object(self.module, "build_menu", return_value=Mock()), \
                patch.object(self.module.GLib, "timeout_add", return_value=42) as start, \
                patch.object(self.module.GLib, "source_remove") as stop:
            self.module.Tray.refresh(client)
            start.assert_called_once_with(125, client.animate)
            self.assertEqual(client.animation_timer, 42)
            client.animate()
            client.indicator.set_icon_full.assert_called_with("drivesync-tray-syncing-1",
                                                              client.icon_description)
            self.module.Tray.refresh(client)
            self.assertEqual(start.call_count, 1)
            client.settings.get_property.return_value = False
            self.module.Tray.refresh(client)
            stop.assert_called_once_with(42)
            self.assertIsNone(client.animation_timer)
            client.indicator.set_icon_full.assert_called_with("drivesync-tray-syncing",
                                                              client.icon_description)
            self.module.Tray.refresh(client)
            self.assertEqual(start.call_count, 1)
            client.settings.get_property.return_value = True
            self.module.Tray.refresh(client)
            self.assertEqual(start.call_count, 2)
            client.status = dict(folders=dict(docs=dict(status="ok")))
            self.module.Tray.refresh(client)
            self.assertIsNone(client.animation_timer)
            self.assertEqual(stop.call_count, 2)
            self.module.Tray.refresh(client)
            self.assertEqual(start.call_count, 2)


class TrayIconTests(unittest.TestCase):
    def test_tray_icons_and_frames_are_explicit_colour_assets(self):
        from xml.etree import ElementTree
        from drivesync.presentation import ICONS, TRAY_ICONS, TRAY_FRAMES

        self.assertEqual(set(TRAY_ICONS), set(ICONS))
        self.assertEqual(len(TRAY_FRAMES), 8)
        names = {candidates[0] for candidates in (*TRAY_ICONS.values(), *TRAY_FRAMES)}
        self.assertEqual(len(names), 14)
        for name in names:
            with self.subTest(icon=name):
                path = support.ROOT / "data/icons/hicolor/scalable/status" / f"{name}.svg"
                self.assertTrue(path.is_file())
                svg = path.read_text()
                self.assertNotIn("currentColor", svg)
                self.assertEqual(ElementTree.fromstring(svg).get("viewBox"), "0 0 22 22")
        for key in ICONS:
            self.assertEqual(TRAY_ICONS[key][1:], ICONS[key])
            self.assertTrue(ICONS[key][0].endswith("-symbolic"))

    def test_tray_resolves_colour_icons_before_symbolic_fallbacks(self):
        from unittest.mock import Mock, patch
        from drivesync import tray
        from drivesync.presentation import TRAY_ICONS, TRAY_FRAMES

        theme = Mock()
        with patch.object(tray.Gtk.IconTheme, "get_default", return_value=theme):
            for candidates in (*TRAY_ICONS.values(), *TRAY_FRAMES):
                with self.subTest(icon=candidates[0]):
                    theme.has_icon.side_effect = lambda name: name in candidates
                    self.assertEqual(tray.Tray.resolve_icon(candidates), candidates[0])
                    theme.has_icon.side_effect = lambda name: name in candidates[1:]
                    self.assertEqual(tray.Tray.resolve_icon(candidates), candidates[1])
                    theme.has_icon.return_value = False
                    theme.has_icon.side_effect = None
                    self.assertEqual(tray.Tray.resolve_icon(candidates), candidates[-1])
