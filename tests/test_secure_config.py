"""Opt-in rclone config encryption. Real rclone, but only against a TEMP config with a `local`
remote, and a FAKE secret store: the real keyring and ~/.config/rclone are never touched."""

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
from unittest.mock import patch

from gi.repository import GLib

from support import IsolatedTest, ROOT
from drivesync import config, doctor, i18n, keyring_store, prerequisites, rclone, remote_watch, secure_config, setup_remote
from drivesync.keyring_store import KeyringLocked, KeyringUnavailable, SecretMissing

REAL_RCLONE = shutil.which("rclone") or str(Path("~/.local/bin/rclone").expanduser())


class FakeStore:
    """Keyring double. The secret also lives in a file so a password-command script can read it."""
    name = "fake"

    def __init__(self, path):
        self.path = Path(path)
        self.locked = False
        self.fail_store = False
        self.fail_delete = False

    def state(self):
        if self.locked:
            raise KeyringLocked("locked")

    def lookup(self):
        self.state()
        if not self.path.exists():
            raise SecretMissing("missing")
        return self.path.read_text()

    def store(self, password):
        self.state()
        if self.fail_store:
            raise KeyringUnavailable("store")
        self.path.write_text(password)

    def delete(self):
        if self.fail_delete:
            raise KeyringUnavailable("delete")
        self.path.unlink(missing_ok=True)


class RealRcloneCase(IsolatedTest):
    def setUp(self):
        super().setUp()
        self.conf = Path(os.environ["RCLONE_CONFIG"])
        self.assertTrue(self.conf.is_relative_to(self.root))
        self.original = "[loc]\ntype = local\n"
        self.conf.write_text(self.original)
        self.conf.chmod(0o600)
        self.config.update(rclone_path=REAL_RCLONE, remote="loc:")
        config.save(self.paths, self.config)
        self.store_ = FakeStore(self.root / "keyring-secret")
        keyring_store.set_backend(self.store_)
        self.addCleanup(keyring_store.set_backend, None)
        # The password command lives in a directory with a space: exercises rclone's quoting too.
        script_dir = self.root / "bin dir"
        script_dir.mkdir()
        script = script_dir / "drivesync-cli"
        script.write_text(f"#!/bin/sh\ncat '{self.store_.path}'\necho\n")
        script.chmod(0o755)
        self.command = secure_config.quote_argument(str(script))
        patcher = patch.object(secure_config, "password_command", lambda: self.command)
        patcher.start()
        self.addCleanup(patcher.stop)

    def listremotes(self, with_password=True):
        settings = config.load(self.paths, create=False)
        env = secure_config.rclone_env(settings if with_password else {}, dict(os.environ))
        if not with_password:
            env.pop("RCLONE_PASSWORD_COMMAND", None)
        return subprocess.run([REAL_RCLONE, "listremotes"], env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=30)


class RoundTripTests(RealRcloneCase):
    def test_enable_then_disable_round_trip(self):
        value = secure_config.enable(self.paths)
        self.assertTrue(value["enabled"])
        self.assertTrue(self.conf.read_text().startswith("# Encrypted rclone configuration"))
        self.assertNotIn("type = local", self.conf.read_text())
        backup = Path(value["backup"])
        self.assertEqual(backup.parent, self.paths.config_dir)
        self.assertTrue(backup.name.startswith("rclone.conf.before-encryption-"))
        self.assertEqual(backup.read_text(), self.original)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertEqual(config.load(self.paths)["rclone_password_command"], self.command)
        self.assertGreaterEqual(len(self.store_.path.read_text()), 40)
        # Works through the password command, fails without it (and without hanging on a prompt).
        self.assertEqual(self.listremotes().stdout, "loc:\n")
        self.assertNotEqual(self.listremotes(with_password=False).returncode, 0)
        status = secure_config.status(self.paths)
        self.assertEqual((status["enabled"], status["encrypted"], status["keyring"], status["backend"]),
                         (True, True, "ok", "fake"))
        with self.assertRaises(ValueError):
            secure_config.enable(self.paths)
        value = secure_config.disable(self.paths)
        self.assertFalse(value["enabled"])
        self.assertEqual(self.conf.read_text().strip(), self.original.strip())
        self.assertFalse(self.store_.path.exists())
        self.assertEqual(config.load(self.paths)["rclone_password_command"], "")
        self.assertEqual(self.listremotes().stdout, "loc:\n")

    def test_cli_requires_yes_and_emits_json(self):
        with patch("drivesync.cli.reload_daemon"):
            code, out, err = self.cli("secure-config", "enable", "--json")
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(out)["error"]["code"], "invalid_request")
            self.assertEqual(self.conf.read_text(), self.original)
            code, out, err = self.cli("secure-config", "enable", "--yes", "--json")
            self.assertEqual(code, 0, err)
            self.assertTrue(json.loads(out)["enabled"])
            code, out, _ = self.cli("secure-config", "status", "--json")
            value = json.loads(out)
            self.assertEqual((value["schema_version"], value["enabled"], value["encrypted"]), (1, True, True))
            self.assertNotIn(self.store_.path.read_text(), out)
            self.assertEqual(self.cli("secure-config", "disable", "--json")[0], 1)
            code, out, _ = self.cli("secure-config", "disable", "--yes", "--json")
            self.assertEqual((code, json.loads(out)["enabled"]), (0, False))

    def test_password_never_in_logs_or_config(self):
        secure_config.enable(self.paths)
        password = self.store_.path.read_text()
        for path in self.paths.config_dir.iterdir():
            if path.is_file():
                self.assertNotIn(password, path.read_text())

    def test_rollback_when_verification_fails(self):
        with patch.object(secure_config, "_remotes", side_effect=[{"loc:"}, {"other:"}]):
            with self.assertRaises(ValueError):
                secure_config.enable(self.paths)
        self.assertEqual(self.conf.read_text(), self.original)
        self.assertFalse(self.store_.path.exists())
        self.assertEqual(config.load(self.paths)["rclone_password_command"], "")
        self.assertEqual(list(self.paths.config_dir.glob("rclone.conf.before-encryption-*")), [])

    def test_rollback_when_rclone_encrypt_fails(self):
        self.command = "/nonexistent/helper-for-test"
        with self.assertRaises(ValueError):
            secure_config.enable(self.paths)
        self.assertEqual(self.conf.read_text(), self.original)
        self.assertFalse(self.store_.path.exists())
        self.assertEqual(config.load(self.paths)["rclone_password_command"], "")

    def test_rollback_when_config_save_fails(self):
        with patch.object(secure_config, "_save_command", side_effect=OSError("disk")):
            with self.assertRaises(ValueError):
                secure_config.enable(self.paths)
        self.assertEqual(self.conf.read_text(), self.original)
        self.assertFalse(self.store_.path.exists())

    def test_keyring_store_failure_changes_nothing(self):
        self.store_.fail_store = True
        with self.assertRaises(ValueError):
            secure_config.enable(self.paths)
        self.assertEqual(self.conf.read_text(), self.original)
        self.assertFalse(self.store_.path.exists())

    def test_preconditions_change_nothing(self):
        self.store_.locked = True
        with self.assertRaisesRegex(ValueError, "kilitli"):
            secure_config.enable(self.paths)
        self.store_.locked = False
        keyring_store.set_backend(None)
        with patch.object(keyring_store, "backend", return_value=None):
            with self.assertRaises(ValueError):
                secure_config.enable(self.paths)
        keyring_store.set_backend(self.store_)
        self.assertEqual(self.conf.read_text(), self.original)
        self.assertEqual(list(self.paths.config_dir.glob("rclone.conf.before-encryption-*")), [])

    def test_missing_config_file_is_refused(self):
        self.conf.unlink()
        with self.assertRaises(ValueError):
            secure_config.enable(self.paths)
        self.assertFalse(self.store_.path.exists())

    def test_disable_with_locked_keyring_keeps_everything(self):
        secure_config.enable(self.paths)
        encrypted = self.conf.read_text()
        self.store_.locked = True
        with self.assertRaises(ValueError):
            secure_config.disable(self.paths)
        self.assertEqual(self.conf.read_text(), encrypted)
        self.assertNotEqual(config.load(self.paths)["rclone_password_command"], "")

    def test_disable_when_not_enabled(self):
        with self.assertRaises(ValueError):
            secure_config.disable(self.paths)

    def test_setup_remote_path_uses_password_command(self):
        # `remotes()` is what setup-remote and the prerequisite check use.
        secure_config.enable(self.paths)
        settings = config.load(self.paths, create=False)
        self.assertEqual(prerequisites.remotes(REAL_RCLONE, secure_config.rclone_env(settings)), {"loc:"})
        with self.assertRaises(prerequisites.SetupError):
            prerequisites.remotes(REAL_RCLONE, self.clean_env())

    def clean_env(self):
        return {k: v for k, v in os.environ.items() if k != "RCLONE_PASSWORD_COMMAND"}


class HelperTests(IsolatedTest):
    class Tty(io.StringIO):
        def isatty(self):
            return True

    def setUp(self):
        super().setUp()
        self.fake = FakeStore(self.root / "secret")
        keyring_store.set_backend(self.fake)
        self.addCleanup(keyring_store.set_backend, None)

    def run_helper(self, out=None):
        out, err = out or io.StringIO(), io.StringIO()
        return secure_config.main_helper(out, err), out.getvalue(), err.getvalue()

    def test_prints_secret_to_pipe(self):
        self.fake.path.write_text("pw-1")
        self.assertEqual(self.run_helper(), (0, "pw-1\n", ""))

    def test_refuses_tty_and_never_prints(self):
        self.fake.path.write_text("pw-1")
        code, out, err = self.run_helper(self.Tty())
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err)

    def test_locked_and_missing_fail_without_output(self):
        code, out, err = self.run_helper()
        self.assertEqual((code, out, err.strip()), (1, "", i18n.KEYRING_MISSING))
        self.fake.path.write_text("pw-1")
        self.fake.locked = True
        code, out, err = self.run_helper()
        self.assertEqual((code, out, err.strip()), (1, "", i18n.KEYRING_LOCKED))
        self.assertNotIn("pw-1", err)

    def test_cli_entry_point(self):
        self.fake.path.write_text("pw-2")
        with patch("sys.stdout", io.StringIO()) as out:
            from drivesync.cli import main
            self.assertEqual(main(["keyring-password"]), 0)
        self.assertEqual(out.getvalue(), "pw-2\n")

    def test_libsecret_backend_without_a_bus_is_unavailable(self):
        # The isolated session bus address points nowhere, so the real keyring is unreachable.
        try:
            backend = keyring_store.LibsecretBackend()
        except ImportError:
            self.skipTest("gir1.2-secret-1 not installed")
        self.assertEqual(keyring_store.probe(backend), "unavailable")
        with self.assertRaises(KeyringUnavailable):
            backend.lookup()

    def test_real_launcher_refuses_without_backend_or_tty(self):
        # Fresh process: no fake store, no keyring in the isolated session; must fail closed, not hang.
        result = subprocess.run([str(ROOT / "bin/drivesync-cli"), "keyring-password"], env=os.environ,
                                capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")


class EnvironmentTests(IsolatedTest):
    COMMAND = "/opt/drivesync/drivesync-cli keyring-password"

    def test_rclone_env_is_the_single_builder(self):
        base = {"A": "1"}
        self.assertEqual(secure_config.rclone_env({}, base), base)
        self.assertEqual(secure_config.rclone_env({"rclone_password_command": ""}, base), base)
        self.assertEqual(secure_config.rclone_env({"rclone_password_command": self.COMMAND}, base),
                         {"A": "1", "RCLONE_PASSWORD_COMMAND": self.COMMAND})
        self.assertNotIn("RCLONE_PASSWORD_COMMAND", os.environ)

    def test_quote_argument(self):
        self.assertEqual(secure_config.quote_argument("/a/b"), "/a/b")
        self.assertEqual(secure_config.quote_argument("/a b/c"), '"/a b/c"')
        self.assertEqual(secure_config.quote_argument('/a"b'), '"/a""b"')

    def test_config_field_validation(self):
        data = dict(self.config, rclone_password_command=self.COMMAND)
        self.assertEqual(config.validate(data)["rclone_password_command"], self.COMMAND)
        self.assertEqual(config.defaults()["rclone_password_command"], "")
        for bad in (1, " x", "a\nb", None):
            with self.assertRaises(config.ConfigError):
                config.validate(dict(self.config, rclone_password_command=bad))

    def encrypted_config(self):
        self.config["rclone_password_command"] = self.COMMAND
        config.save(self.paths, self.config)
        return config.load(self.paths)

    def test_async_process_delivers_env(self):
        log = self.root / "env.log"
        log.touch()
        code, output = rclone.run_sync(["/bin/sh", "-c", "echo [$RCLONE_PASSWORD_COMMAND]"], log,
                                       env=secure_config.rclone_env(self.encrypted_config()))
        self.assertEqual(code, 0)
        self.assertIn("[" + self.COMMAND + "]", output)

    def test_prerequisites_and_doctor_pass_env(self):
        settings = self.encrypted_config()
        seen = []
        def record(command, **kwargs):
            seen.append((command[1:3], kwargs.get("env")))
            out = "rclone v1.75.0" if command[1] == "version" else "test:\n"
            return subprocess.CompletedProcess(command, 0, out, "")
        with patch("drivesync.prerequisites.subprocess.run", side_effect=record):
            prerequisites.check(settings)
        self.assertEqual(seen[-1][0][:1], ["listremotes"])
        self.assertEqual(seen[-1][1]["RCLONE_PASSWORD_COMMAND"], self.COMMAND)

    def test_setup_remote_passes_env_everywhere(self):
        client = self.root / "client.json"
        client.write_text(json.dumps({"installed": {"client_id": "x.apps.googleusercontent.com",
                                                    "client_secret": "dummy"}}))
        self.encrypted_config()
        seen = []
        def record(command, **kwargs):
            seen.append((command[1], kwargs.get("env", {}) and kwargs["env"].get("RCLONE_PASSWORD_COMMAND")))
            out = "rclone v1.75.0" if command[1] == "version" else ""
            return subprocess.CompletedProcess(command, 0, out, "")
        with patch("drivesync.setup_remote.subprocess.run", side_effect=record), \
             patch("drivesync.setup_remote.check_version"), \
             patch("drivesync.setup_remote.remotes", side_effect=lambda b, env=None: seen.append(("remotes", env["RCLONE_PASSWORD_COMMAND"])) or set()):
            with contextlib.redirect_stdout(io.StringIO()):
                setup_remote.setup(self.paths, client, "newdrive")
        self.assertTrue(len(seen) >= 3)
        for name, value in seen:
            self.assertEqual(value, self.COMMAND, name)

    def test_remote_watch_default_runner_passes_env(self):
        settings = self.encrypted_config()
        checker = remote_watch.RemoteChecker(self.paths, lambda: settings)
        with patch("drivesync.remote_watch.run_capture") as capture:
            checker.runner(["rclone", "x"], 5, lambda *_: None)
        self.assertEqual(capture.call_args.kwargs["env"]["RCLONE_PASSWORD_COMMAND"], self.COMMAND)

    def test_cli_direct_runs_pass_env(self):
        self.encrypted_config()
        self.prepare()
        with patch("drivesync.cli.dbus_service.call", return_value=True), \
             patch.object(secure_config, "gate", return_value=None):
            code, out, err = self.cli("init", "docs", "--dry-run", "--json")
        self.assertEqual(code, 0, err)
        calls = self.calls()
        self.assertTrue(calls)
        self.assertEqual({c["password_command"] for c in calls}, {self.COMMAND})

    def test_cli_direct_run_refuses_locked_keyring(self):
        self.encrypted_config()
        with patch.object(secure_config, "gate", return_value="locked"):
            code, out, err = self.cli("init", "docs", "--dry-run", "--json")
        self.assertEqual(code, 1)
        self.assertIn(i18n.KEYRING_LOCKED, out)
        self.assertEqual(self.calls(), [])

    def test_unencrypted_config_sets_no_env(self):
        self.prepare()
        with patch("drivesync.cli.dbus_service.call", return_value=True):
            self.assertEqual(self.cli("init", "docs", "--dry-run", "--json")[0], 0)
        self.assertEqual({c["password_command"] for c in self.calls()}, {None})


class DoctorTests(IsolatedTest):
    def setUp(self):
        super().setUp()
        self.fake = FakeStore(self.root / "secret")
        keyring_store.set_backend(self.fake)
        self.addCleanup(keyring_store.set_backend, None)
        self.calls_ = []

    def fake_run(self, argv, **kwargs):
        self.calls_.append((argv, kwargs.get("env")))
        out = {"version": "rclone v1.75.0", "listremotes": "test:\n"}.get(argv[1], "")
        code = 0
        if argv[1:4] == ["config", "encryption", "check"]:
            code = 0 if self.encrypted else 2
        return subprocess.CompletedProcess(argv, code, out, "Error: config file is NOT encrypted" if code == 2 else "")

    def report(self, **settings):
        self.config.update(settings)
        config.save(self.paths, self.config)
        with patch("drivesync.doctor.run", side_effect=self.fake_run), \
             patch("drivesync.doctor.gi_available", return_value=True), \
             patch("drivesync.secure_config._run", side_effect=lambda b, a, e, timeout=0: self.fake_run([b, *a], env=e)):
            value = doctor.report(self.paths, offline=True)
        return {c["id"]: c for c in value["checks"]}

    def test_not_encrypted_warns_with_fix(self):
        self.encrypted = False
        checks = self.report()
        self.assertEqual(checks["rclone.config_encrypted"]["status"], "warn")
        self.assertEqual(checks["rclone.config_encrypted"]["fix"], "drivesync-cli secure-config enable --yes")
        self.assertEqual(checks["keyring.available"]["status"], "ok")
        self.assertEqual(checks["keyring.available"]["details"]["backend"], "fake")

    def test_encrypted_and_keyring_ok(self):
        self.encrypted = True
        checks = self.report(rclone_password_command="/x/drivesync-cli keyring-password")
        self.assertEqual(checks["rclone.config_encrypted"]["status"], "ok")
        self.assertEqual(checks["keyring.available"]["status"], "ok")
        listing = next(env for argv, env in self.calls_ if argv[1] == "listremotes")
        self.assertEqual(listing["RCLONE_PASSWORD_COMMAND"], "/x/drivesync-cli keyring-password")

    def test_locked_and_missing_backend_are_warnings_unless_required(self):
        self.encrypted = False
        self.fake.locked = True
        self.assertEqual(self.report()["keyring.available"]["status"], "warn")
        self.assertEqual(self.report()["keyring.available"]["details"]["state"], "locked")
        with patch.object(keyring_store, "backend", return_value=None):
            checks = self.report()
        self.assertEqual((checks["keyring.available"]["status"], checks["keyring.available"]["details"]["backend"]),
                         ("warn", None))

    def test_required_but_unreadable_fails(self):
        self.encrypted = False
        with patch.object(keyring_store, "backend", return_value=None):
            checks = self.report(rclone_password_command="/x/drivesync-cli keyring-password")
        self.assertEqual(checks["keyring.available"]["status"], "fail")


class DaemonKeyringTests(IsolatedTest):
    def make(self, state):
        from drivesync.daemon import Daemon
        from drivesync.notify import Notifier
        self.config.update(rclone_password_command="/x/drivesync-cli keyring-password", check_updates=False)
        config.save(self.paths, self.config)
        self.prepare(initialized=True)
        self.state = state
        patcher = patch.object(secure_config, "gate", side_effect=lambda _c: self.state)
        patcher.start()
        self.addCleanup(patcher.stop)
        with patch("drivesync.daemon.Notifier", return_value=Notifier(send=lambda *_: None)):
            daemon = Daemon(self.paths)
        self.addCleanup(lambda: daemon.child.kill() if daemon.child else None)
        daemon.reload_config()
        self.addCleanup(daemon.watchers.close)
        daemon.next_poll = float("inf")
        return daemon

    def test_locked_keyring_waits_without_folder_errors(self):
        daemon = self.make("locked")
        self.assertTrue(daemon.config_valid)
        value = daemon.store.read()
        self.assertEqual(value["daemon"]["status"], "keyring_locked")
        self.assertEqual(value["daemon"]["last_error"], i18n.KEYRING_LOCKED)
        self.assertTrue(daemon.sync_now("docs"))
        daemon._pump()
        self.assertIsNone(daemon.child)
        self.assertFalse(daemon.queue.pending)
        folder = daemon.store.read()["folders"]["docs"]
        self.assertNotEqual(folder["status"], "error")
        self.assertIsNone(folder["last_error"])
        self.assertEqual(self.calls(), [])
        self.assertEqual(i18n.STATUSES["keyring_locked"], "Anahtar zinciri kilitli")

    def test_unlock_resumes_and_runs_with_password_command(self):
        daemon = self.make("locked")
        self.state = None
        daemon._keyring_tick(float("inf"))
        self.assertIsNone(daemon.keyring_state)
        self.assertTrue(daemon.pending_reload)
        daemon.reload_config()
        value = daemon.store.read()["daemon"]
        self.assertEqual((value["status"], value["last_error"]), ("idle", None))
        self.assertTrue(daemon.sync_now("docs"))
        daemon._pump()
        from test_daemon import drain_until
        timer = GLib.timeout_add(25, daemon._tick)
        try:
            drain_until(lambda: daemon.child is None and not daemon.queue.pending)
        finally:
            GLib.source_remove(timer)
        calls = self.calls()
        self.assertTrue(calls)
        self.assertEqual({c["password_command"] for c in calls}, {"/x/drivesync-cli keyring-password"})

    def test_missing_secret_is_an_error_not_a_hang(self):
        daemon = self.make("missing")
        self.assertEqual(daemon.store.read()["daemon"]["status"], "error")
        self.assertEqual(daemon.store.read()["daemon"]["last_error"], i18n.KEYRING_MISSING)
        daemon.sync_now("docs")
        daemon._pump()
        self.assertIsNone(daemon.child)

    def test_tray_presentation_for_locked_keyring(self):
        from drivesync import presentation
        view = presentation.present({"daemon": {"status": "keyring_locked"}, "folder_config": [], "folders": {}})
        self.assertEqual(view.text if hasattr(view, "text") else view[0], i18n.KEYRING_LOCKED)
