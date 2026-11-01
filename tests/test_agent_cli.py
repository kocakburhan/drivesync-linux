"""Agent contracts: isolated config/state, fake processes, no cloud or systemd jobs."""

from contextlib import redirect_stderr
import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from support import IsolatedTest, ROOT
from drivesync import cli, config, doctor, filters, i18n
from drivesync.state import execution_lock


class AgentCliTests(IsolatedTest):
    def value(self, *args, code=0):
        actual, out, err = self.cli(*args, "--json")
        self.assertEqual(actual, code, err or out)
        value = json.loads(out)
        self.assertEqual(value["schema_version"], 1)
        return value

    def test_folder_list_and_add_shapes_and_reload(self):
        self.assertEqual(self.value("folder", "list")["folders"], self.config["folders"])
        local = self.root / "Another Folder"
        local.mkdir()
        with patch("drivesync.cli.dbus_service.call", return_value=True) as call:
            value = self.value("folder", "add", str(local), "--remote-name", "Diğer")
        call.assert_called_once_with("ReloadConfig")
        folder = value["folder"]
        self.assertEqual(folder["local"], str(local))
        self.assertEqual(folder["remote_name"], "Diğer")
        self.assertTrue(folder["enabled"])
        self.assertFalse(folder["initialized"])
        self.assertEqual(len(self.value("folder", "list")["folders"]), 2)

    def test_add_folder_compatibility_alias_json(self):
        local = self.root / "Other"
        local.mkdir()
        self.assertEqual(self.value("add-folder", str(local), "--name", "Other")["folder"]["id"], "other")

    def test_folder_validation_matches_gui_and_never_changes_config(self):
        root = Path(self.config["folders"][0]["local"])
        nested = root / "nested"
        nested.mkdir()
        link = self.root / "link"
        link.symlink_to(root, target_is_directory=True)
        file = self.root / "file"
        file.touch()
        other = self.root / "other"
        other.mkdir()
        before = self.paths.config.read_bytes()
        for path, name in ((root, "Other"), (nested, "Other"), (self.root, "Other"),
                           (link, "Other"), (self.root / "missing", "Other"), (file, "Other"),
                           (other, "Documents"), (other, "../bad"), (other, "a:b"),
                           (other, " name "), (other, ""), (Path("/"), "Root")):
            with self.subTest(path=path, name=name):
                self.assertFalse(self.value("folder", "add", str(path), "--remote-name", name, code=1)["ok"])
                self.assertEqual(self.paths.config.read_bytes(), before)

    def test_folder_enable_disable_and_reload(self):
        with patch("drivesync.cli.dbus_service.call", return_value=True) as call:
            for action, enabled in (("disable", False), ("enable", True)):
                self.assertEqual(self.value("folder", action, "docs")["folder"]["enabled"], enabled)
                self.assertEqual(config.load(self.paths)["folders"][0]["enabled"], enabled)
        self.assertEqual(call.call_count, 2)

    def test_folder_remove_requires_yes_and_preserves_data_and_state(self):
        local = Path(self.config["folders"][0]["local"])
        sentinel = local / "keep.txt"
        sentinel.write_text("user data")
        self.store.folder("docs", last_success="previous")
        before = self.paths.state.read_bytes()
        self.assertFalse(self.value("folder", "remove", "docs", code=1)["ok"])
        result = self.value("folder", "remove", "docs", "--yes")
        self.assertTrue(result["removed"])
        self.assertEqual(result["id"], "docs")
        self.assertEqual(config.load(self.paths)["folders"], [])
        self.assertEqual(sentinel.read_text(), "user data")
        self.assertEqual(self.paths.state.read_bytes(), before)
        self.assertEqual(self.calls(), [])

    def test_unknown_folder_operations_have_machine_errors(self):
        for command in (("folder", "remove", "unknown", "--yes"), ("folder", "enable", "unknown"),
                        ("folder", "disable", "unknown"), ("progress", "unknown")):
            value = self.value(*command, code=1)
            self.assertEqual(value["error"]["code"], "invalid_request")

    def test_config_all_scalar_keys_validate_persist_and_reload(self):
        values = dict(root_folder="My Computer", poll_interval_minutes=2.5, debounce_seconds=0,
                      max_delete_percent=20, transfers=2, init_transfers=12, checkers=16)
        with patch("drivesync.cli.dbus_service.call", return_value=True) as call:
            for key, value in values.items():
                result = self.value("config", "set", key, str(value))
                self.assertEqual(result["key"], key)
                self.assertEqual(result["value"], value)
                self.assertEqual(self.value("config", "get", key)["value"], value)
        self.assertEqual(call.call_count, len(values))

    def test_config_rejects_bad_scalars_without_writing(self):
        before = self.paths.config.read_bytes()
        for key, value in (("root_folder", "../bad"), ("root_folder", ""),
                           ("poll_interval_minutes", "0"), ("poll_interval_minutes", "-1"),
                           ("debounce_seconds", "-1"), ("max_delete_percent", "101"),
                           ("max_delete_percent", "-1"), ("transfers", "0"), ("transfers", "1.5"),
                           ("init_transfers", "false"), ("checkers", "null"),
                           ("checkers", "text"), ("debounce_seconds", "NaN"),
                           ("debounce_seconds", "Infinity")):
            with self.subTest(key=key, value=value):
                self.assertFalse(self.value("config", "set", key, value, code=1)["ok"])
                self.assertEqual(self.paths.config.read_bytes(), before)

    def test_config_cannot_set_unsafe_keys(self):
        for key in ("rclone_path", "remote", "max_monitors", "paused", "init_extra_flags", "folders"):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                cli.main(["config", "set", key, "x"])
            self.assertEqual(error.exception.code, 2)

    def test_config_excludes_list_add_remove_and_validation(self):
        original = self.value("config", "excludes", "list")["patterns"]
        with patch("drivesync.cli.dbus_service.call", return_value=True) as call:
            for _ in range(2):
                patterns = self.value("config", "excludes", "add", "*.private")["patterns"]
                self.assertEqual(patterns.count("*.private"), 1)
            self.assertEqual(self.value("config", "excludes", "remove", "*.private")["patterns"], original)
        self.assertEqual(call.call_count, 3)
        for pattern in ("", " x ", "a\nb", "a\x00b"):
            self.assertFalse(self.value("config", "excludes", "add", pattern, code=1)["ok"])
        self.assertFalse(self.value("config", "excludes", "remove", "missing", code=1)["ok"])
        self.assertEqual(self.value("config", "excludes", "list")["patterns"], original)

    def test_config_edits_invalidate_preview(self):
        for command in (("config", "set", "root_folder", "Changed"),
                        ("config", "excludes", "add", "*.private")):
            self.assertEqual(self.cli("init", "docs", "--dry-run")[0], 0)
            self.assertEqual(self.cli(*command)[0], 0)
            self.assertEqual(self.cli("init", "docs", "--yes")[0], 1)

    def test_progress_uses_live_state_and_has_idle_defaults(self):
        self.assertIsNone(self.value("progress", "docs")["progress"])
        stats = dict(bytes_done=20, bytes_total=100, speed=2.0, eta_seconds=40, updated_at="now")
        self.store.folder("docs", status="syncing", progress=stats)
        value = self.value("progress", "docs")
        self.assertEqual(value["status"], "syncing")
        self.assertEqual(value["progress"], stats)
        self.assertEqual(value["id"], "docs")
        self.assertIn("docs", self.cli("progress", "docs")[1])

    def test_status_json_with_and_without_daemon(self):
        offline = self.value("status")
        self.assertEqual(offline["folder_config"], self.config["folders"])
        self.assertEqual(offline["daemon"]["status"], "error")
        value = dict(offline, paused=True)
        with patch("drivesync.cli.dbus_service.call", return_value=json.dumps(value)):
            self.assertEqual(self.value("status"), value)

    def test_reads_do_not_create_config_or_state(self):
        self.paths.config.unlink()
        for command in (("config", "get", "root_folder"), ("config", "excludes", "list"),
                        ("folder", "list"), ("status",)):
            self.value(*command)
            self.assertFalse(self.paths.config.exists())
            self.assertFalse(self.paths.state.exists())

    def test_all_confirming_commands_require_yes_in_tty_and_non_tty(self):
        for tty in (False, True):
            with patch("sys.stdin.isatty", return_value=tty), patch("builtins.input", side_effect=AssertionError("prompt")), \
                 patch("drivesync.cli.direct") as direct, patch("drivesync.cli.setup") as setup:
                for command in (("init", "docs"), ("init", "docs", "--background"),
                                ("resync", "docs"), ("force-sync", "docs"),
                                ("folder", "remove", "docs"),
                                ("setup-remote", "--client-json", "/unused", "--replace")):
                    code, _, err = self.cli(*command)
                    self.assertEqual(code, 1)
                    self.assertIn("--yes", err)
                direct.assert_not_called()
                setup.assert_not_called()

    def test_non_tty_read_and_edit_commands_never_prompt(self):
        with patch("sys.stdin.isatty", return_value=False), patch("builtins.input", side_effect=AssertionError("prompt")):
            for command in (("folder", "list"), ("config", "get", "root_folder"),
                            ("config", "set", "transfers", "3"), ("progress", "docs"), ("status",)):
                self.assertEqual(self.cli(*command)[0], 0)

    def test_background_builds_detached_command_and_returns_unit_json(self):
        self.assertEqual(self.cli("init", "docs", "--dry-run", "--json")[0], 0)
        with patch("drivesync.cli.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            value = self.value("init", "docs", "--background", "--yes")
        self.assertEqual(value["unit"], "drivesync-init-docs.service")
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["systemd-run", "--user", "--unit=drivesync-init-docs", "--collect"])
        self.assertNotIn("--wait", command)
        self.assertEqual(command[-5:], ["--", str(ROOT / "bin/drivesync-cli"), "init", "docs", "--yes"])
        self.assertIn("--setenv=XDG_CONFIG_HOME=" + self.env["XDG_CONFIG_HOME"], command)
        self.assertEqual(run.call_args.kwargs["timeout"], 15)
        self.assertFalse(config.load(self.paths)["folders"][0]["initialized"])

    def test_background_requires_current_successful_preview_and_enabled_folder(self):
        with patch("drivesync.cli.subprocess.run") as run:
            self.assertFalse(self.value("init", "docs", "--background", "--yes", code=1)["ok"])
            run.assert_not_called()
        self.assertEqual(self.cli("init", "docs", "--dry-run")[0], 0)
        self.config["root_folder"] = "Changed"
        config.save(self.paths, self.config)
        with patch("drivesync.cli.subprocess.run") as run:
            self.value("init", "docs", "--background", "--yes", code=1)
            self.config["folders"][0]["enabled"] = False
            config.save(self.paths, self.config)
            self.value("init", "docs", "--background", "--yes", code=1)
            run.assert_not_called()

    def test_background_refuses_held_lock_invalid_flags_and_unknown_id(self):
        with patch("drivesync.cli.subprocess.run") as run:
            with execution_lock(self.paths, "docs"):
                self.assertEqual(self.value("init", "docs", "--background", "--yes", code=1)["error"]["code"], "busy")
            for command in (("init", "docs", "--background", "--dry-run"),
                            ("init", "unknown", "--background", "--yes")):
                self.value(*command, code=1)
            run.assert_not_called()

    def test_background_failure_and_timeout_do_not_expose_child_output(self):
        self.assertEqual(self.cli("init", "docs", "--dry-run")[0], 0)
        failures = (subprocess.CompletedProcess([], 1, "private-secret", "private-secret"),
                    subprocess.TimeoutExpired("secret-command", 15), FileNotFoundError())
        for result in failures:
            with patch("drivesync.cli.subprocess.run", **({"side_effect": result} if isinstance(result, Exception)
                                                         else {"return_value": result})):
                value = self.value("init", "docs", "--background", "--yes", code=1)
                self.assertNotIn("secret", json.dumps(value))

    def test_background_uses_installed_launcher_and_preserves_environment_arguments(self):
        self.assertEqual(self.cli("init", "docs", "--dry-run")[0], 0)
        prefix = self.root / "custom prefix"
        launcher = prefix / "bin/drivesync-cli"
        launcher.parent.mkdir(parents=True)
        launcher.touch()
        with patch("drivesync.cli.__file__", str(prefix / "lib/drivesync/drivesync/cli.py")), \
             patch.dict(os.environ, RCLONE_CONFIG=str(self.root / "config with spaces")), \
             patch("drivesync.cli.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            code, out, err = self.cli("init", "docs", "--background", "--yes")
        self.assertEqual(code, 0, err)
        self.assertIn("drivesync-init-docs.service", out)
        command = run.call_args.args[0]
        self.assertEqual(command[-5:], ["--", str(launcher), "init", "docs", "--yes"])
        self.assertIn("--setenv=RCLONE_CONFIG=" + str(self.root / "config with spaces"), command)

    def test_json_dry_run_success_and_failure_contract(self):
        value = self.value("init", "docs", "--dry-run")
        for key in ("to_remote", "to_local", "deletes", "unknown"):
            self.assertIs(type(value[key]), int)
        self.assertIsInstance(value["actions"], list)
        self.settings(dry_run=dict(code=5, log="ERROR : failure"))
        self.assertFalse(self.value("init", "docs", "--dry-run", code=1)["ok"])


class DoctorTests(IsolatedTest):
    def fake_run(self, argv, **kwargs):
        if argv[0] == "systemctl":
            output = "enabled" if argv[2] == "is-enabled" else "active"
        elif argv[1] == "version":
            output = "rclone v1.66.0"
        elif argv[1] == "listremotes":
            output = "test:\nother:"
        else:
            output = "private-token-never-print"
        return subprocess.CompletedProcess(argv, 0, output, "private-secret-never-print")

    def report(self, *, offline=False, run=None):
        with patch("drivesync.doctor.run", side_effect=run or self.fake_run) as process, \
             patch("drivesync.doctor.gi_available", return_value=True):
            value = doctor.report(self.paths, offline=offline)
        return {item["id"]: item for item in value["checks"]}, value, process

    def install_fakes(self):
        prefix = Path.home() / ".local"
        for name in ("drivesync-cli", "drivesync-daemon", "drivesync-app", "drivesync-tray"):
            path = prefix / "bin" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        for name in (*(module + ".py" for module in doctor.INSTALLED_MODULES), "style.css"):
            path = prefix / "lib/drivesync/drivesync" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        for name in ("applications/io.github.kocakburhan.DriveSync.desktop", "nautilus-python/extensions/drivesync.py",
                     "icons/hicolor/scalable/apps/io.github.kocakburhan.DriveSync.svg"):
            path = self.paths.data_dir.parent / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        for name in doctor.UNITS:
            path = self.paths.config_dir.parent / "systemd/user" / (name + ".service")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()

    def test_full_schema_check_ids_exit_and_redaction(self):
        self.install_fakes()
        checks, value, process = self.report()
        self.assertEqual(set(checks), set(doctor.CHECK_IDS))
        self.assertTrue(value["ok"])
        self.assertEqual(value["schema_version"], 1)
        for item in checks.values():
            self.assertEqual(set(item), {"id", "status", "message", "fix", "details"})
            self.assertIn(item["status"], ("ok", "warn", "fail"))
            self.assertTrue(item["message"])
            self.assertTrue(item["fix"])
        self.assertNotIn("private-", json.dumps(value))
        about = next(c for c in process.call_args_list if c.args[0][1] == "about")
        self.assertEqual(about.kwargs["timeout"], 8)
        self.assertIn("--retries", about.args[0])
        for ok, code in ((True, 0), (False, 1)):
            with patch("drivesync.doctor.report", return_value=dict(value, ok=ok)):
                actual, out, _ = self.cli("doctor", "--json")
                self.assertEqual(actual, code)
                self.assertEqual(json.loads(out)["ok"], ok)

    def test_offline_never_probes_remote_and_does_not_mutate_files(self):
        before = self.paths.config.read_bytes()
        checks, value, process = self.report(offline=True)
        self.assertTrue(checks["remote.reachable"]["details"]["skipped"])
        self.assertEqual(checks["remote.reachable"]["status"], "warn")
        self.assertFalse(any(c.args[0][1] == "about" for c in process.call_args_list))
        self.assertEqual(self.paths.config.read_bytes(), before)
        self.assertFalse(self.paths.state.exists())
        self.paths.config.unlink()
        checks, _, _ = self.report(offline=True)
        self.assertEqual(checks["config.valid"]["status"], "fail")
        self.assertFalse(self.paths.config.exists())

    def test_missing_typelibs_have_individual_ids_and_gnome_hint(self):
        with patch.dict(os.environ, XDG_CURRENT_DESKTOP="GNOME", XDG_SESSION_TYPE="wayland"), \
             patch("drivesync.doctor.run", side_effect=self.fake_run), \
             patch("drivesync.doctor.gi_available", return_value=False):
            checks = {c["id"]: c for c in doctor.report(self.paths, offline=True)["checks"]}
        for check_id in doctor.GI_IDS:
            self.assertEqual(checks[check_id]["status"], "fail")
            self.assertIn("sudo apt", checks[check_id]["fix"])
        self.assertEqual(checks["desktop.appindicator"]["status"], "warn")
        self.assertIn("AppIndicator", checks["desktop.appindicator"]["details"]["hint"])
        self.assertEqual(checks["environment.desktop"]["details"]["session_type"], "wayland")

    def test_version_remote_and_systemd_failures_are_safe(self):
        for failure in ("version", "listremotes", "about", "systemctl"):
            def failing(argv, **kwargs):
                if failure in argv:
                    return subprocess.CompletedProcess(argv, 1, "private-token", "private-secret")
                return self.fake_run(argv, **kwargs)
            checks, value, _ = self.report(run=failing)
            check_id = {"version": "rclone.version", "listremotes": "remote.configured",
                        "about": "remote.reachable", "systemctl": "systemd.drivesync.active"}[failure]
            self.assertEqual(checks[check_id]["status"], "fail")
            self.assertNotIn("private-", json.dumps(value))
        def too_old(argv, **kwargs):
            if argv[1] == "version":
                return subprocess.CompletedProcess(argv, 0, "rclone v1.65.9", "")
            return self.fake_run(argv, **kwargs)
        self.assertEqual(self.report(run=too_old)[0]["rclone.version"]["status"], "fail")

    def test_missing_timeout_and_subprocess_errors_are_bounded(self):
        for failure in (FileNotFoundError(), subprocess.TimeoutExpired("secret", 5)):
            with patch("drivesync.doctor.subprocess.run", side_effect=failure):
                self.assertIsNone(doctor.run(["fake"]))
        with patch("drivesync.doctor.run", return_value=None), patch("drivesync.doctor.gi_available", return_value=False):
            value = doctor.report(self.paths)
            self.assertFalse(value["ok"])

    def test_invalid_config_and_state_report_fail_without_dumping_content(self):
        self.paths.config.write_text('{"secret":"never-print"')
        checks, value, _ = self.report(offline=True)
        self.assertEqual(checks["config.valid"]["status"], "fail")
        self.assertNotIn("never-print", json.dumps(value))
        config.save(self.paths, self.config)
        self.paths.state.write_text("invalid")
        checks, _, _ = self.report(offline=True)
        self.assertEqual(checks["state.folders"]["status"], "fail")

    def test_inotify_estimate_ignores_excludes_disabled_folders_and_symlinks(self):
        root = Path(self.config["folders"][0]["local"])
        (root / "nested/deep").mkdir(parents=True)
        (root / "node_modules/ignored").mkdir(parents=True)
        (root / "link").symlink_to(root, target_is_directory=True)
        self.assertEqual(doctor.estimate_watches(self.config), (3, True))
        self.assertEqual(doctor.estimate_watches(self.config, cap=2), (2, False))
        self.config["folders"][0]["enabled"] = False
        self.assertEqual(doctor.estimate_watches(self.config), (0, True))

    def test_inotify_low_limit_and_folder_summary(self):
        self.config["max_monitors"] = 1
        (Path(self.config["folders"][0]["local"]) / "nested").mkdir()
        config.save(self.paths, self.config)
        self.store.folder("docs", status="syncing", progress=dict(bytes=5))
        checks, _, _ = self.report(offline=True)
        self.assertEqual(checks["inotify.watches"]["status"], "warn")
        self.assertEqual(checks["inotify.watches"]["details"]["estimated_need"], 2)
        self.assertEqual(checks["state.folders"]["details"]["folders"][0]["progress"], dict(bytes=5))

    def test_gi_probes_are_separate_processes(self):
        with patch("drivesync.doctor.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            for namespace, version in doctor.GI_TYPES:
                self.assertTrue(doctor.gi_available(namespace, version))
        self.assertEqual(len(run.call_args_list), len(doctor.GI_TYPES))
        gtk3 = run.call_args_list[doctor.GI_IDS.index("gi.Gtk3")].args[0][-1]
        gtk4 = run.call_args_list[doctor.GI_IDS.index("gi.Gtk4")].args[0][-1]
        self.assertIn("'3.0'", gtk3)
        self.assertIn("'4.0'", gtk4)

    def test_plain_doctor_has_every_id_and_fix(self):
        _, report, _ = self.report(offline=True)
        with patch("drivesync.doctor.report", return_value=report):
            code, output, error = self.cli("doctor", "--offline")
        self.assertEqual(code, 1)
        self.assertFalse(error)
        for item in report["checks"]:
            self.assertIn(item["id"], output)
            self.assertIn(item["fix"], output)

    def test_missing_installed_module_and_units_are_named(self):
        self.install_fakes()
        module = Path.home() / ".local/lib/drivesync/drivesync/doctor.py"
        module.unlink()
        unit = self.paths.config_dir.parent / "systemd/user/drivesync.service"
        unit.unlink()
        checks, _, _ = self.report(offline=True)
        self.assertEqual(checks["install.files"]["details"]["missing"], [str(module)])
        self.assertEqual(checks["install.units"]["details"]["missing"], [str(unit)])

    def test_unsupported_os_and_python_are_reported(self):
        with patch("drivesync.doctor.sys.platform", "darwin"), \
             patch("drivesync.doctor.sys.version_info", (3, 11)):
            checks, _, _ = self.report(offline=True)
        self.assertEqual(checks["environment.os"]["status"], "fail")
        self.assertEqual(checks["python.version"]["status"], "fail")

    def test_kernel_watch_limit_and_unreadable_limit_are_warnings(self):
        original = Path.read_text
        for result in ("0", OSError()):
            def read(path, *args, **kwargs):
                if str(path) == "/proc/sys/fs/inotify/max_user_watches":
                    if isinstance(result, Exception):
                        raise result
                    return result
                return original(path, *args, **kwargs)
            with patch.object(Path, "read_text", read):
                checks, _, _ = self.report(offline=True)
            self.assertEqual(checks["inotify.watches"]["status"], "warn")

    def test_failed_folder_state_affects_doctor_exit(self):
        self.prepare(initialized=True)
        self.store.folder("docs", status="blocked_delete")
        checks, value, _ = self.report(offline=True)
        self.assertEqual(checks["state.folders"]["status"], "fail")
        self.assertFalse(value["ok"])

    def test_doctor_bootstraps_without_site_packages_or_gi(self):
        # -S removes distro GI too. All operations remain inside the test home.
        result = subprocess.run([sys.executable, "-S", str(ROOT / "bin/drivesync-cli"),
                                 "doctor", "--offline", "--json"], env=os.environ,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1, result.stderr)
        checks = {c["id"]: c for c in json.loads(result.stdout)["checks"]}
        # The parent starts without GI; probes use a normal isolated interpreter,
        # which may still find the distro typelibs. Either result is valid.
        self.assertIn(checks["gi.GLib"]["status"], ("ok", "fail"))
        self.assertTrue(checks["remote.reachable"]["details"]["skipped"])

    def test_agents_docs_cover_every_check_and_cli_subcommand(self):
        docs = (ROOT / "AGENTS.md").read_text()
        for check_id in doctor.CHECK_IDS:
            self.assertIn('`' + check_id + '`', docs)
        def commands(parser, prefix="drivesync-cli"):
            for action in parser._actions:
                if isinstance(action, argparse._SubParsersAction):
                    for name, child in action.choices.items():
                        command = prefix + " " + name
                        yield command
                        yield from commands(child, command)
        for command in commands(cli.parser()):
            self.assertIn(command, docs)
