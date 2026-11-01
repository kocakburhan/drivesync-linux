"""Notification action buttons, with a fake libnotify module and mocked process launches."""

import types
import unittest
from unittest.mock import patch

from support import IsolatedTest  # noqa: F401  (puts src/ on sys.path)
from drivesync import i18n
from drivesync.notify import APP_ID, Notifier, open_window


class FakeNotification:
    def __init__(self, title, body, icon):
        self.title, self.body, self.icon = title, body, icon
        self.actions = []
        self.callbacks = {}
        self.handlers = {}
        self.shown = False

    def add_action(self, action, label, callback, user_data):
        self.actions.append((action, label))
        self.callbacks[action] = (callback, user_data)

    def connect(self, signal, handler, *data):
        self.handlers[signal] = (handler, data)

    def show(self):
        self.shown = True

    def close(self):
        handler, data = self.handlers["closed"]
        handler(self, *data)


class FakeNotify:
    """Stands in for gi.repository.Notify: records created notifications and server capabilities."""

    def __init__(self, caps=("actions", "body")):
        self.caps = list(caps)
        self.created = []
        self.Notification = types.SimpleNamespace(new=self._new)

    def _new(self, title, body, icon):
        notification = FakeNotification(title, body, icon)
        self.created.append(notification)
        return notification

    def get_server_caps(self):
        return list(self.caps)


class NotifyActionTests(IsolatedTest):
    def setUp(self):
        super().setUp()
        self.launches = []
        i18n.set_language("tr")
        self.addCleanup(i18n.set_language, None)

    def notifier(self, caps=("actions", "body"), **kwargs):
        notifier = Notifier(launch=lambda: self.launches.append(True), **kwargs)
        notifier.notify = FakeNotify(caps)
        return notifier

    def error_item(self, **changes):
        return dict({"status": "error", "needs_resync": False, "last_error": "boom"}, **changes)

    def only(self, notifier):
        created = notifier.notify.created
        self.assertEqual(len(created), 1)
        return created[0]

    def test_error_and_resync_notifications_get_review_button(self):
        notifier = self.notifier()
        notifier.folder("docs", "Documents", self.error_item())
        notification = self.only(notifier)
        self.assertEqual(notification.actions, [("review", "İncele")])
        self.assertTrue(notification.shown)

        notifier = self.notifier()
        notifier.folder("docs", "Documents", self.error_item(status="ok", needs_resync=True))
        self.assertEqual(self.only(notifier).actions, [("review", "İncele")])

        notifier = self.notifier()
        notifier.folder("docs", "Documents", self.error_item(status="blocked_delete"))
        self.assertEqual(self.only(notifier).actions, [("review", "İncele")])

    def test_needs_approval_and_init_failure_get_review_button(self):
        notifier = self.notifier()
        notifier.init_needs_approval("Documents")
        self.assertEqual(self.only(notifier).actions, [("review", "İncele")])

        notifier = self.notifier()
        notifier.init_failed("Documents", "boom")
        self.assertEqual(self.only(notifier).actions, [("review", "İncele")])

    def test_conflicts_notification_gets_show_conflicts_button(self):
        notifier = self.notifier()
        notifier.folder("docs", "Documents", {"status": "ok", "needs_resync": False, "conflicts": 2})
        notification = self.only(notifier)
        self.assertEqual(notification.actions, [("conflicts", "Çakışmaları göster")])
        i18n.set_language("en")
        notifier = self.notifier()
        notifier.folder("docs", "Documents", {"status": "ok", "needs_resync": False, "conflicts": 2})
        self.assertEqual(self.only(notifier).actions, [("conflicts", "Show conflicts")])

    def test_offline_notification_has_no_button(self):
        notifier = self.notifier()
        notifier.offline(1800)
        notification = self.only(notifier)
        self.assertEqual(notification.body, i18n.OFFLINE)
        self.assertEqual(notification.actions, [])

    def test_server_without_actions_capability_gets_plain_notifications(self):
        notifier = self.notifier(caps=("body",))
        notifier.folder("docs", "Documents", self.error_item())
        notification = self.only(notifier)
        self.assertTrue(notification.shown)
        self.assertEqual(notification.actions, [])

    def test_cli_notifier_never_adds_buttons(self):
        notifier = self.notifier(actions=False)
        notifier.folder("docs", "Documents", self.error_item())
        self.assertEqual(self.only(notifier).actions, [])

    def test_capability_is_checked_once(self):
        notifier = self.notifier()
        calls = []
        notifier.notify.get_server_caps = lambda: calls.append(1) or ["actions"]
        notifier.folder("docs", "Documents", self.error_item())
        notifier.init_failed("Documents", "boom")
        self.assertEqual(len(calls), 1)

    def test_button_callback_launches_window(self):
        notifier = self.notifier()
        notifier.folder("docs", "Documents", self.error_item())
        notification = self.only(notifier)
        callback, user_data = notification.callbacks["review"]
        callback(notification, "review", user_data)
        self.assertEqual(self.launches, [True])

    def test_referenced_until_closed_then_dropped(self):
        notifier = self.notifier()
        notifier.folder("docs", "Documents", self.error_item())
        notification = self.only(notifier)
        self.assertEqual(len(notifier._live), 1)
        notification.close()
        self.assertEqual(notifier._live, {})

    def test_live_references_are_bounded(self):
        notifier = self.notifier()
        for number in range(40):
            notifier.init_failed(f"Folder {number}", "boom")
        self.assertEqual(len(notifier._live), 16)
        self.assertEqual(len(notifier.notify.created), 40)

    def test_default_launch_runs_drivesync_app_without_shell(self):
        with patch("drivesync.notify.shutil.which", side_effect=lambda name: f"/home/u/.local/bin/{name}"), \
                patch("drivesync.notify.subprocess.Popen") as popen:
            self.assertTrue(open_window())
        argv = popen.call_args.args[0]
        self.assertEqual(argv, ["/home/u/.local/bin/drivesync-app"])
        self.assertNotIn("shell", popen.call_args.kwargs)

    def test_default_launch_falls_back_to_gtk_launch_with_app_id(self):
        def which(name):
            return None if name == "drivesync-app" else "/usr/bin/gtk-launch"
        with patch("drivesync.notify.shutil.which", side_effect=which), \
                patch("drivesync.notify.subprocess.Popen") as popen:
            self.assertTrue(open_window())
        self.assertEqual(popen.call_args.args[0], ["/usr/bin/gtk-launch", APP_ID])
        self.assertEqual(APP_ID, "io.github.kocakburhan.DriveSync")

    def test_no_launcher_starts_nothing(self):
        with patch("drivesync.notify.shutil.which", return_value=None), \
                patch("drivesync.notify.subprocess.Popen") as popen:
            self.assertFalse(open_window())
        popen.assert_not_called()

    def test_button_click_uses_default_launcher(self):
        notifier = Notifier()
        notifier.notify = FakeNotify()
        notifier.folder("docs", "Documents", self.error_item())
        notification = self.only(notifier)
        callback, user_data = notification.callbacks["review"]
        with patch("drivesync.notify.shutil.which", return_value="/usr/bin/drivesync-app"), \
                patch("drivesync.notify.subprocess.Popen") as popen:
            callback(notification, "review", user_data)
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0], ["/usr/bin/drivesync-app"])


if __name__ == "__main__":
    unittest.main()
