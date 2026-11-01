"""`drivesync-cli update`: refusals and command sequence with subprocess mocked. No real git, systemd or network."""

import json
import subprocess
from unittest.mock import patch

from support import IsolatedTest
from drivesync import __version__, i18n, updates


class FakeCheckout:
    """Answers the handful of commands the update may run; everything else succeeds silently."""

    def __init__(self, source):
        self.source = str(source)
        self.calls = []
        self.answers = {
            ("git", "status", "--porcelain"): (0, "", ""),
            ("git", "symbolic-ref", "-q", "HEAD"): (0, "refs/heads/main\n", ""),
            ("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): (0, "origin/main\n", ""),
            ("git", "pull", "--ff-only"): (0, "Already up to date.\n", ""),
            (self.source + "/install.sh",): (0, "DriveSync kuruldu.\n", ""),
            ("systemctl", "--user", "restart", "drivesync.service", "drivesync-tray.service"): (0, "", ""),
        }

    def run(self, command, **kwargs):
        key = tuple(command)
        self.calls.append((key, str(kwargs.get("cwd")), kwargs.get("env", {}).get("GIT_TERMINAL_PROMPT")))
        if kwargs.get("timeout") is None or kwargs.get("check") is not False:
            raise AssertionError("every update command needs a timeout and check=False")
        answer = self.answers.get(key, (0, "", ""))
        if isinstance(answer, BaseException):
            raise answer
        code, out, err = answer
        return subprocess.CompletedProcess(command, code, out, err)

    def names(self):
        return [call[0] for call in self.calls]


class UpdateCliTests(IsolatedTest):
    def setUp(self):
        super().setUp()
        self.source = self.root / "checkout"
        self.source.mkdir()
        (self.source / "install.sh").write_text("#!/bin/sh\n")
        self.info = self.root / "install-info.json"
        self.info.write_text(json.dumps(dict(source_dir=str(self.source), version=__version__,
                                             installed_at="2026-11-01T00:00:00+00:00")))
        patcher = patch("drivesync.updates.install_info_path", return_value=self.info)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.git = FakeCheckout(self.source)
        patcher = patch("drivesync.cli.subprocess.run", side_effect=self.git.run)
        self.run = patcher.start()
        self.addCleanup(patcher.stop)
        self.sequence = [("git", "status", "--porcelain"),
                         ("git", "symbolic-ref", "-q", "HEAD"),
                         ("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"),
                         ("git", "pull", "--ff-only"),
                         (str(self.source / "install.sh"),),
                         ("systemctl", "--user", "restart", "drivesync.service", "drivesync-tray.service")]

    def start_sync(self, status="syncing", **extra):
        self.store.folder("docs", status=status, **extra)

    def test_check_reports_versions_and_runs_no_command(self):
        with patch("drivesync.updates.fetch_latest", return_value=dict(latest="99.0.0", url="https://github.com/x")):
            code, out, err = self.cli("update", "--check", "--json")
        self.assertEqual(code, 0, err)
        value = json.loads(out)
        self.assertEqual(value["current"], __version__)
        self.assertEqual(value["latest"], "99.0.0")
        self.assertTrue(value["update_available"])
        self.assertEqual(value["url"], "https://github.com/x")
        self.assertEqual(self.git.calls, [])

    def test_check_human_output_and_up_to_date(self):
        with patch("drivesync.updates.fetch_latest", return_value=dict(latest=__version__, url="https://github.com/x")):
            code, out, _ = self.cli("update", "--check")
        self.assertEqual(code, 0)
        self.assertIn(i18n.UPDATE_UP_TO_DATE, out)

    def test_check_network_failure_is_a_clean_error(self):
        with patch("drivesync.updates.fetch_latest", side_effect=updates.UpdateError("URLError")):
            code, out, err = self.cli("update", "--check")
            self.assertEqual(code, 1)
            self.assertIn(i18n.UPDATE_CHECK_FAILED, err)
            self.assertNotIn("Traceback", err)
            code, out, err = self.cli("update", "--check", "--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(err or out)["error"]["code"], "operation_failed")
        self.assertEqual(self.git.calls, [])

    def test_update_without_yes_refuses_before_anything_runs(self):
        code, out, err = self.cli("update")
        self.assertEqual(code, 1)
        self.assertIn(i18n.YES_REQUIRED, err)
        self.assertEqual(self.git.calls, [])

    def test_missing_or_broken_install_record_refuses(self):
        self.info.unlink()
        code, _, err = self.cli("update", "--yes")
        self.assertEqual(code, 1)
        self.assertIn(i18n.UPDATE_NO_INFO, err)
        self.info.write_text("{not json")
        code, _, err = self.cli("update", "--yes")
        self.assertEqual(code, 1)
        self.assertIn(i18n.UPDATE_NO_INFO, err)
        self.info.write_text(json.dumps(dict(source_dir="relative/path")))
        self.assertEqual(self.cli("update", "--yes")[0], 1)
        self.assertEqual(self.git.calls, [])

    def test_directory_without_install_script_is_not_a_checkout(self):
        (self.source / "install.sh").unlink()
        code, _, err = self.cli("update", "--yes")
        self.assertEqual(code, 1)
        self.assertIn(i18n.UPDATE_NOT_CHECKOUT.format(path=self.source), err)
        self.assertEqual(self.git.calls, [])

    def test_git_failure_means_not_a_checkout_and_stops(self):
        self.git.answers[("git", "status", "--porcelain")] = (128, "", "fatal: not a git repository")
        code, _, err = self.cli("update", "--yes")
        self.assertEqual(code, 1)
        self.assertIn(i18n.UPDATE_NOT_CHECKOUT.format(path=self.source), err)
        self.assertEqual(self.git.names(), [self.sequence[0]])

    def test_uncommitted_changes_refuse_before_pull(self):
        self.git.answers[("git", "status", "--porcelain")] = (0, " M src/drivesync/cli.py\n", "")
        code, _, err = self.cli("update", "--yes")
        self.assertEqual(code, 1)
        self.assertIn(i18n.UPDATE_DIRTY.format(path=self.source), err)
        self.assertEqual(self.git.names(), [self.sequence[0]])

    def test_detached_head_refuses(self):
        self.git.answers[("git", "symbolic-ref", "-q", "HEAD")] = (1, "", "")
        code, _, err = self.cli("update", "--yes")
        self.assertEqual(code, 1)
        self.assertIn(i18n.UPDATE_DETACHED, err)
        self.assertEqual(self.git.names(), self.sequence[:2])

    def test_branch_must_track_origin(self):
        for upstream in ("upstream/main", "", None):
            with self.subTest(upstream=upstream):
                self.git.calls.clear()
                if upstream is None:
                    self.git.answers[self.sequence[2]] = (1, "", "no upstream")
                else:
                    self.git.answers[self.sequence[2]] = (0, upstream + "\n", "")
                code, _, err = self.cli("update", "--yes")
                self.assertEqual(code, 1)
                self.assertIn(i18n.UPDATE_NO_UPSTREAM, err)
                self.assertNotIn(("git", "pull", "--ff-only"), self.git.names())

    def test_running_sync_refuses_and_force_restart_proceeds_with_warning(self):
        self.start_sync("syncing")
        code, _, err = self.cli("update", "--yes")
        self.assertEqual(code, 1)
        self.assertIn(i18n.UPDATE_BUSY, err)
        self.assertNotIn(("git", "pull", "--ff-only"), self.git.names())
        self.git.calls.clear()
        code, _, err = self.cli("update", "--yes", "--force-restart")
        self.assertEqual(code, 0, err)
        self.assertIn(i18n.UPDATE_FORCE_WARNING, err)
        self.assertEqual(self.git.names(), self.sequence)
        self.git.calls.clear()
        code, out, err = self.cli("update", "--yes", "--force-restart", "--json")
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["forced_restart"])
        self.assertEqual(err, "")

    def test_queued_initial_sync_counts_as_running_but_waiting_approval_does_not(self):
        self.start_sync("init_queued", init_request=dict(kind="init"))
        self.assertEqual(self.cli("update", "--yes")[0], 1)
        self.store.folder("docs", status="needs_approval", init_request=dict(kind="init"))
        self.git.calls.clear()
        self.assertEqual(self.cli("update", "--yes")[0], 0)
        self.assertEqual(self.git.names(), self.sequence)

    def test_full_sequence_uses_checkout_cwd_and_never_prompts(self):
        code, out, err = self.cli("update", "--yes", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.git.names(), self.sequence)
        for _, cwd, prompt in self.git.calls:
            self.assertEqual(cwd, str(self.source))
            self.assertEqual(prompt, "0")
        value = json.loads(out)
        self.assertEqual(value, dict(schema_version=1, updated=True, previous=__version__,
                                     version=__version__, forced_restart=False))

    def test_human_success_prints_one_line_and_no_raw_output(self):
        code, out, err = self.cli("update", "--yes")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), i18n.UPDATE_DONE.format(version=__version__))
        self.assertNotIn("Already up to date", out + err)

    def test_pull_failure_stops_before_install(self):
        self.git.answers[self.sequence[3]] = (1, "", "fatal: Not possible to fast-forward")
        code, _, err = self.cli("update", "--yes")
        self.assertEqual(code, 1)
        self.assertIn(i18n.UPDATE_PULL_FAILED, err)
        self.assertEqual(self.git.names(), self.sequence[:4])

    def test_install_failure_stops_before_restart(self):
        self.git.answers[self.sequence[4]] = (1, "", "Hata: rclone >= 1.66 gerekli.")
        code, _, err = self.cli("update", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("Hata: rclone >= 1.66 gerekli.", err)
        self.assertIn(i18n.UPDATE_INSTALL_FAILED, err)
        self.assertEqual(self.git.names(), self.sequence[:5])

    def test_restart_failure_and_timeouts_are_clean_errors(self):
        self.git.answers[self.sequence[5]] = (5, "", "Failed to restart")
        code, out, err = self.cli("update", "--yes", "--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["error"]["code"], "operation_failed")
        self.assertIn(i18n.UPDATE_RESTART_FAILED, json.loads(out)["error"]["message"])
        for position, message in ((3, i18n.UPDATE_PULL_FAILED), (4, i18n.UPDATE_INSTALL_FAILED)):
            with self.subTest(position=position):
                self.git = FakeCheckout(self.source)
                self.run.side_effect = self.git.run
                self.git.answers[self.sequence[position]] = subprocess.TimeoutExpired(self.sequence[position], 900)
                code, _, err = self.cli("update", "--yes")
                self.assertEqual(code, 1)
                self.assertIn(message, err)
                self.assertNotIn("Traceback", err)

    def test_commands_run_only_inside_the_recorded_checkout(self):
        code, _, _ = self.cli("update", "--yes")
        self.assertEqual(code, 0)
        for _, cwd, _ in self.git.calls:
            self.assertEqual(cwd, str(self.source))
            self.assertTrue(cwd.startswith(str(self.root)))
