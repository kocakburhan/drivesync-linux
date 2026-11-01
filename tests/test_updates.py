"""Release check: version rules, throttling, state and notification. Never touches the network."""

from datetime import datetime, timedelta, timezone
import json
import socket
import threading
import time
import unittest
import urllib.error
from unittest.mock import Mock, patch

from gi.repository import GLib

from support import IsolatedTest
from drivesync import config, i18n, updates
from drivesync.daemon import Daemon
from drivesync.notify import Notifier
from drivesync.state import StateStore


class Clock:
    def __init__(self, moment):
        self.moment = moment

    def __call__(self):
        return self.moment

    def advance(self, **delta):
        self.moment += timedelta(**delta)


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, size=-1):
        return self.body[:size] if size and size > 0 else self.body


def release(tag="v0.3.0", url="https://github.com/kocakburhan/drivesync-linux/releases/tag/v0.3.0"):
    return json.dumps({"tag_name": tag, "html_url": url}).encode()


class VersionTests(unittest.TestCase):
    def test_parse_accepts_v_prefix_and_rejects_everything_else(self):
        self.assertEqual(updates.parse_version("v0.2.0"), (0, 2, 0))
        self.assertEqual(updates.parse_version("10.0.1"), (10, 0, 1))
        for text in ("v0.2", "v1.0.0-rc1", "latest", "", None, 3, "v0.2.0.1"):
            with self.subTest(text=text):
                self.assertIsNone(updates.parse_version(text))

    def test_is_newer_compares_numbers_not_text(self):
        for latest, current, expected in (
                ("v0.3.0", "0.2.0", True),
                ("v0.2.0", "0.2.0", False),
                ("v0.2.1", "0.2.0", True),
                ("v0.10.0", "0.9.9", True),
                ("v0.2.10", "0.2.9", True),
                ("v0.2.1", "0.2.10", False),
                ("v0.1.9", "0.2.0", False),
                ("garbage", "0.2.0", False),
                (None, "0.2.0", False),
                ("v0.3.0", "unknown", False)):
            with self.subTest(latest=latest, current=current):
                self.assertIs(updates.is_newer(latest, current), expected)


class FetchTests(unittest.TestCase):
    def fetch(self, opener):
        return updates.fetch_latest("0.2.0", opener=opener)

    def test_request_shape_and_result(self):
        seen = {}

        def opener(request, timeout):
            seen.update(url=request.full_url, timeout=timeout, agent=request.get_header("User-agent"),
                        auth=request.get_header("Authorization"))
            return FakeResponse(release())

        result = self.fetch(opener)
        self.assertEqual(seen["url"], "https://api.github.com/repos/kocakburhan/drivesync-linux/releases/latest")
        self.assertEqual(seen["timeout"], 10)
        self.assertEqual(seen["agent"], "DriveSync/0.2.0")
        self.assertIsNone(seen["auth"])
        self.assertEqual(result["latest"], "0.3.0")
        self.assertEqual(result["url"], "https://github.com/kocakburhan/drivesync-linux/releases/tag/v0.3.0")

    def test_foreign_or_missing_page_url_falls_back_to_release_page(self):
        result = self.fetch(lambda request, timeout: FakeResponse(release(url="http://evil.example/")))
        self.assertEqual(result["url"], updates.RELEASE_PAGE)

    def test_every_failure_is_one_update_error_without_the_url(self):
        failures = (
            urllib.error.URLError("no route to host"),
            socket.timeout("timed out"),
            urllib.error.HTTPError(updates.LATEST_API, 403, "rate limited", None, None),
        )
        for error in failures:
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(updates.UpdateError) as caught:
                    self.fetch(Mock(side_effect=error))
                self.assertNotIn("api.github.com", str(caught.exception))
        bodies = (b"not json", b"[]", json.dumps({"tag_name": "v0.3"}).encode(),
                  json.dumps({"name": "x"}).encode(), b"\xff\xfe")
        for body in bodies:
            with self.subTest(body=body):
                with self.assertRaises(updates.UpdateError):
                    self.fetch(lambda request, timeout, body=body: FakeResponse(body))


class ThrottleTests(IsolatedTest):
    def checker(self, clock, fetch=None):
        store = StateStore(self.paths)
        notify = Mock()
        return updates.UpdateChecker(store, notify, clock=clock, fetch=fetch, current="0.2.0"), notify

    def test_at_most_one_lookup_per_24_hours_on_a_fake_clock(self):
        clock = Clock(datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc))
        checker, _ = self.checker(clock)
        lookups = []
        for _ in range(60 * 60):  # 60 hours of 1-minute polls
            if checker.due(True):
                checker.begin()
                lookups.append(clock())
            clock.advance(minutes=1)
        self.assertEqual(len(lookups), 3)  # 09:00, then +24 h and +48 h
        self.assertEqual(lookups[1] - lookups[0], timedelta(hours=24))

    def test_due_waits_exactly_one_day_and_disabled_never_checks(self):
        clock = Clock(datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc))
        checker, _ = self.checker(clock)
        self.assertTrue(checker.due(True))
        self.assertFalse(checker.due(False))
        checker.begin()
        clock.advance(hours=23, minutes=59)
        self.assertFalse(checker.due(True))
        clock.advance(minutes=1)
        self.assertTrue(checker.due(True))
        self.assertFalse(checker.due(False))

    def test_throttle_survives_a_restart_because_it_is_stored(self):
        clock = Clock(datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc))
        checker, _ = self.checker(clock)
        checker.begin()
        clock.advance(hours=2)
        restarted, _ = self.checker(clock)
        self.assertFalse(restarted.due(True))
        stored = StateStore(self.paths).read()["update"]
        self.assertEqual(stored["checked_at"], "2026-11-01T09:00:00+00:00")

    def test_failed_attempt_is_still_throttled_and_stores_nothing_new(self):
        clock = Clock(datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc))
        checker, notify = self.checker(clock)
        checker.begin()
        with self.assertLogs(level="WARNING") as logs:
            checker.finish(updates.UpdateError("URLError"))
            checker.finish(updates.UpdateError("URLError"))
        self.assertEqual(len(logs.output), 1)  # logged once, not on every failure
        self.assertIn(i18n.UPDATE_CHECK_FAILED, logs.output[0])
        stored = StateStore(self.paths).read()["update"]
        self.assertIsNone(stored["latest"])
        self.assertIsNotNone(stored["checked_at"])
        notify.assert_not_called()
        self.assertFalse(checker.due(True))

    def test_success_is_stored_announced_once_and_failure_logging_resets(self):
        clock = Clock(datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc))
        checker, notify = self.checker(clock)
        checker.begin()
        with self.assertLogs(level="WARNING"):
            checker.finish(updates.UpdateError("first"))
        checker.finish(updates.UpdateError("again"))  # silent: already logged
        outcome = dict(latest="0.3.0", url="https://github.com/x/y")
        checker.finish(outcome)
        stored = StateStore(self.paths).read()["update"]
        self.assertEqual((stored["latest"], stored["url"], stored["notified"]), ("0.3.0", outcome["url"], "0.3.0"))
        notify.assert_called_once_with("0.3.0", outcome["url"], updates.start_update)
        checker.finish(outcome)
        checker.finish(dict(latest="0.3.0", url=outcome["url"]))
        notify.assert_called_once()
        with self.assertLogs(level="WARNING"):
            checker.finish(updates.UpdateError("later"))  # a success re-arms the failure log
        clock.advance(hours=24)
        checker.begin()
        checker.finish(dict(latest="0.4.0", url="https://github.com/x/z"))
        self.assertEqual(notify.call_count, 2)
        self.assertEqual(notify.call_args.args[0], "0.4.0")

    def test_older_or_equal_release_is_recorded_but_never_announced(self):
        clock = Clock(datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc))
        checker, notify = self.checker(clock)
        checker.finish(dict(latest="0.2.0", url="https://github.com/x/y"))
        checker.finish(dict(latest="0.1.9", url="https://github.com/x/y"))
        notify.assert_not_called()
        self.assertEqual(StateStore(self.paths).read()["update"]["latest"], "0.1.9")

    def test_state_without_update_section_is_upgraded(self):
        self.store.update(lambda value: value.pop("update"))
        value = StateStore(self.paths).read()
        self.assertEqual(value["update"], {"latest": None, "url": None, "checked_at": None, "notified": None})

    def test_check_updates_config_defaults_on_and_must_be_boolean(self):
        self.assertTrue(config.defaults()["check_updates"])
        data = config.load(self.paths)
        self.assertTrue(data["check_updates"])
        for value in ("yes", 1, None):
            with self.subTest(value=value):
                with self.assertRaises(config.ConfigError):
                    config.validate(dict(data, check_updates=value))
        self.assertFalse(config.validate(dict(data, check_updates=False))["check_updates"])


class DaemonGlueTests(IsolatedTest):
    def drain_until(self, predicate, timeout=5):
        context = GLib.MainContext.default()
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                raise AssertionError("update check did not finish")
            context.iteration(False)

    def test_fetch_runs_in_a_worker_and_results_apply_on_the_main_loop(self):
        with patch("drivesync.daemon.Notifier", return_value=Notifier(send=lambda *_: None)):
            daemon = Daemon(self.paths)
        self.addCleanup(daemon.watchers.close)
        threads = []

        def fetch():
            threads.append(threading.current_thread())
            return dict(latest="9.0.0", url="https://github.com/x/y")

        daemon.updates.fetch = fetch
        daemon.updates.notify = Mock()
        daemon._check_updates()
        self.drain_until(lambda: daemon.update_thread is None)
        self.assertIsNot(threads[0], threading.main_thread())
        self.assertEqual(StateStore(self.paths).read()["update"]["latest"], "9.0.0")
        daemon.updates.notify.assert_called_once()
        # The throttle now holds: a second tick does not start another lookup.
        daemon._check_updates()
        self.assertIsNone(daemon.update_thread)
        self.assertEqual(len(threads), 1)

    def test_disabled_check_never_starts_a_thread(self):
        with patch("drivesync.daemon.Notifier", return_value=Notifier(send=lambda *_: None)):
            daemon = Daemon(self.paths)
        self.addCleanup(daemon.watchers.close)
        daemon.config["check_updates"] = False
        daemon.updates.fetch = Mock(side_effect=AssertionError("must not fetch"))
        daemon._check_updates()
        self.assertIsNone(daemon.update_thread)
        daemon.updates.fetch.assert_not_called()


class NotifierTests(unittest.TestCase):
    def test_update_announcement_text_and_fallback_without_actions(self):
        sent = []
        notifier = Notifier(send=lambda title, body: sent.append((title, body)))
        notifier.update_available("0.3.0", "https://github.com/x/y", lambda: None)
        self.assertEqual(sent, [(i18n.UPDATE_TITLE.format(version="0.3.0"),
                                 i18n.UPDATE_BODY.format(url="https://github.com/x/y"))])
        self.assertIn("0.3.0", sent[0][0])


class StartUpdateTests(unittest.TestCase):
    def test_runs_the_cli_in_a_transient_unit_outside_the_daemon(self):
        with patch("drivesync.updates.cli_path", return_value="/home/u/.local/bin/drivesync-cli"), \
                patch("drivesync.updates.subprocess.run") as run:
            self.assertTrue(updates.start_update())
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["systemd-run", "--user", "--collect", "--quiet"])
        self.assertTrue(command[4].startswith("--unit=" + updates.UPDATE_UNIT_PREFIX + "-"))
        self.assertEqual(command[5:], ["/home/u/.local/bin/drivesync-cli", "update", "--yes"])

    def test_missing_cli_or_systemd_run_is_reported_not_raised(self):
        with patch("drivesync.updates.cli_path", return_value=None):
            with self.assertLogs(level="WARNING"):
                self.assertFalse(updates.start_update())
        with patch("drivesync.updates.cli_path", return_value="/x/drivesync-cli"), \
                patch("drivesync.updates.subprocess.run", side_effect=FileNotFoundError("systemd-run")):
            with self.assertLogs(level="WARNING"):
                self.assertFalse(updates.start_update())


if __name__ == "__main__":
    unittest.main()
