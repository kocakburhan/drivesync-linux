"""Every test isolates HOME, XDG paths, bus and rclone."""

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import select
import subprocess
import tempfile
import unittest
from unittest.mock import patch

os.environ["DRIVESYNC_LANG"] = "tr"  # existing assertions are Turkish; test_i18n covers English
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from drivesync import config, filters
from drivesync.paths import Paths
from drivesync.state import StateStore, execution_lock


class IsolatedTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="drivesync-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {
            "DRIVESYNC_LANG": "tr",
            "HOME": str(self.root / "home"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_STATE_HOME": str(self.root / "state"),
            "XDG_DATA_HOME": str(self.root / "data"),
            "XDG_CACHE_HOME": str(self.root / "cache"),
            "XDG_RUNTIME_DIR": str(self.root / "runtime"),
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + str(self.root / "no-bus"),
            "DRIVESYNC_PREFIX": str(self.root / "home/.local"),
            "DRIVESYNC_SYSTEMD_DIR": str(self.root / "config/systemd/user"),
            "RCLONE_CONFIG": str(self.root / "rclone.conf"),
            "FAKE_RCLONE_ROOT": str(self.root),
            "FAKE_RCLONE_RECORD": str(self.root / "calls.jsonl"),
        }
        for name in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
            Path(self.env[name]).mkdir(mode=0o700)
        environment = patch.dict(os.environ, self.env)
        environment.start()
        self.addCleanup(environment.stop)
        self.paths = Paths.from_env()
        self.paths.ensure()
        self.config = config.defaults()
        self.config.update(rclone_path=str(ROOT / "tests/fixtures/fake_rclone.py"), remote="test:",
                           folders=[{"id": "docs", "local": str(self.root / "Documents"),
                                     "remote_name": "Documents", "enabled": True, "initialized": False}])
        Path(self.config["folders"][0]["local"]).mkdir()
        config.save(self.paths, self.config)
        self.store = StateStore(self.paths)

    def settings(self, **settings):
        (self.root / "fake-settings.json").write_text(json.dumps(settings))

    def calls(self, phase="start"):
        path = self.root / "calls.jsonl"
        if not path.exists():
            return []
        return [item for line in path.read_text().splitlines()
                if (item := json.loads(line))["phase"] == phase]

    def prepare(self, initialized=False):
        with execution_lock(self.paths):
            digest = filters.prepare(self.paths, self.config, self.store)["docs"]
        if initialized:
            self.config["folders"][0]["initialized"] = True
            config.save(self.paths, self.config)
            self.store.folder("docs", status="ok")
        return digest

    def cli(self, *args):
        from drivesync.cli import main
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    @contextmanager
    def external_folder_lock(self, *, engine=False):
        script = """
import sys
from drivesync.paths import Paths
from drivesync.state import execution_lock, folder_lock
paths = Paths.from_env()
lock = execution_lock(paths, 'docs') if sys.argv[1] == 'engine' else folder_lock(paths, 'docs')
with lock:
    print('locked', flush=True)
    sys.stdin.read()
"""
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        process = subprocess.Popen([sys.executable, "-c", script, "engine" if engine else "folder"],
                                   env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(select.select([process.stdout], [], [], 5)[0], "lock process timed out")
            self.assertEqual(process.stdout.readline().strip(), "locked")
            yield
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()
