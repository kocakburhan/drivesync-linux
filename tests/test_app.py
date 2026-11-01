import os
import subprocess
import sys
import ast
import configparser
import shutil

from support import IsolatedTest, ROOT


class AppTests(IsolatedTest):
    def test_desktop_wm_class_matches_application_id(self):
        # Do not import GTK4 into a process that may already use GTK3.
        tree = ast.parse((ROOT / "src/drivesync/app.py").read_text())
        app_id = next(ast.literal_eval(node.value) for node in tree.body
                      if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "APP_ID" for target in node.targets))
        desktop = configparser.ConfigParser()
        desktop.read(ROOT / "data/io.github.kocakburhan.DriveSync.desktop")
        self.assertEqual(desktop["Desktop Entry"]["StartupWMClass"], app_id)

    def test_gtk4_window_and_dialogs_in_separate_process(self):
        runner = shutil.which("dbus-run-session")
        if runner is None:
            self.skipTest("GTK smoke requires dbus-run-session for an isolated session bus")
        result = subprocess.run([runner, "--", sys.executable, str(ROOT / "tests/app_smoke.py")],
                                env=dict(os.environ, **self.env), text=True, capture_output=True, timeout=15)
        if result.returncode == 127 and "Failed to start message bus" in result.stderr:
            self.skipTest("GTK smoke cannot start an isolated session bus: " + result.stderr.splitlines()[0])
        if result.returncode == 77:
            self.skipTest(result.stdout.strip())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_launcher_help_imports_without_display_or_user_data_access(self):
        # --help is handled by GApplication before startup, so no config is read.
        result = subprocess.run([sys.executable, str(ROOT / "bin/drivesync-app"), "--help"],
                                env=dict(os.environ, **self.env), text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.calls())
