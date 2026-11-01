import os
import shutil
import subprocess
import sys

from support import IsolatedTest, ROOT


class SessionBusTests(IsolatedTest):
    def session_environment(self):
        # Pass the patched environment explicitly: GI/GLib and Python can have
        # different views of the process environment after earlier tests.
        return {**os.environ, **self.env,
                "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT / "tests")))}

    def test_smoke_script_environment_and_imports_from_repo_root(self):
        self.prepare(initialized=True)
        result = subprocess.run([sys.executable, str(ROOT / "tests/session_smoke.py"),
                                 "--check-environment"], env=self.session_environment(), cwd=ROOT,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_isolated_daemon_with_gdbus(self):
        if not shutil.which("dbus-run-session") or not shutil.which("gdbus"):
            self.skipTest("dbus-run-session or gdbus unavailable")
        self.prepare(initialized=True)
        self.settings(bisync={"sleep": 0.6})
        result = subprocess.run(["dbus-run-session", "--", sys.executable,
                                 str(ROOT / "tests/session_smoke.py")],
                                env=self.session_environment(), cwd=ROOT,
                                capture_output=True, text=True, timeout=35)
        if result.returncode and ("Failed to bind socket" in result.stderr or
                                   "Failed to start message bus" in result.stderr):
            self.skipTest("Session bus unavailable in sandbox: " + result.stderr.splitlines()[0])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
