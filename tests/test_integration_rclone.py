"""Real bisync against local temporary trees, with no user config or cloud access."""

import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import sys
import unittest

from support import ROOT  # Also makes the checkout's src importable.
from drivesync import config, filters, rclone
from drivesync.paths import Paths


RCLONE = Path(os.environ.get("DRIVESYNC_TEST_RCLONE") or shutil.which("rclone") or "~/.local/bin/rclone").expanduser().absolute()


def setUpModule():
    if not RCLONE.is_file() or not os.access(RCLONE, os.X_OK):
        raise unittest.SkipTest(f"Real rclone unavailable: {RCLONE}")


class RealRcloneTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="drivesync-real-rclone-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        # A whitelist also prevents inherited RCLONE_* options (such as
        # --copy-links, remote credentials or config overrides) changing the run.
        self.env = {"PATH": os.defpath, "LANG": "C.UTF-8", "DRIVESYNC_LANG": "tr"}
        for name in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
                     "XDG_CACHE_HOME", "XDG_RUNTIME_DIR", "TMPDIR"):
            directory = self.root / name.lower()
            directory.mkdir(mode=0o700)
            self.env[name] = str(directory)
        empty_config = self.root / "empty-rclone.conf"
        empty_config.touch(mode=0o600)
        self.env["RCLONE_CONFIG"] = str(empty_config)
        self.paths = Paths(self.root / "config", self.root / "state", self.root / "data")
        self.paths.ensure()
        self.config = config.defaults()
        self.config.update(rclone_path=str(RCLONE), remote=str(self.root / "remote") + "/")
        self.folder = dict(id="local-pair", local=str(self.root / "local"),
                           remote_name="Documents", enabled=True, initialized=False)
        self.local = Path(self.folder["local"])
        self.remote = Path(config.remote_path(self.config, self.folder))
        for directory in (self.local, self.remote):
            directory.mkdir(parents=True)
        self.paths.filters.write_text(filters.render(self.config["excludes"]), encoding="utf-8")
        self.logs = []

    def write(self, side, name, content, *, mtime=None):
        path = side / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def run_bisync(self, *, resync=False, dry_run=False, success=True):
        log = rclone.new_log(self.paths, self.folder["id"])
        command = rclone.bisync_command(self.paths, self.config, self.folder, log,
                                        resync=resync, dry_run=dry_run)
        # Both arguments must stay local even if defaults change later.
        self.assertEqual(command[2:4], [str(self.local), str(self.remote)])
        self.assertNotIn(":", command[2] + command[3])
        process = subprocess.run(command, env=self.env, cwd=self.root,
                                 capture_output=True, text=True, timeout=30)
        text = log.read_text(encoding="utf-8")
        self.logs.append(text)
        diagnostic = text + process.stdout + process.stderr
        if success:
            self.assertEqual(process.returncode, 0, diagnostic)
        else:
            self.assertNotEqual(process.returncode, 0, diagnostic)
        return rclone.parse_log(log, process.returncode, dry_run=dry_run)

    def initialize(self):
        # Mirrors CLI initialization's two access markers without a cloud backend.
        marker = rclone.create_check(self.folder)
        self.write(self.remote, rclone.CHECK_FILENAME, marker.read_text(encoding="utf-8"))
        result = self.run_bisync(resync=True)
        self.folder["initialized"] = True
        return result

    def test_initial_dry_run_counts_both_directions_without_mutation(self):
        self.write(self.local, "local one.txt", "local")
        self.write(self.local, "nested/local two.txt", "local two")
        self.write(self.remote, "remote.txt", "remote")
        result = self.run_bisync(resync=True, dry_run=True)
        self.assertEqual((result.to_remote, result.to_local, result.deletes, result.unknown),
                         (2, 1, 0, 0), self.logs[-1])
        self.assertFalse((self.local / "remote.txt").exists())
        self.assertFalse((self.remote / "local one.txt").exists())
        self.assertFalse((self.local / rclone.CHECK_FILENAME).exists())
        self.assertFalse((self.remote / rclone.CHECK_FILENAME).exists())

    def test_real_init_then_new_file_each_direction(self):
        self.write(self.local, "initial local.txt", "local")
        self.write(self.remote, "initial remote.txt", "remote")
        self.initialize()
        self.assertEqual((self.remote / "initial local.txt").read_text(), "local")
        self.assertEqual((self.local / "initial remote.txt").read_text(), "remote")
        self.write(self.local, "new local.txt", "new local")
        self.write(self.remote, "new remote.txt", "new remote")
        result = self.run_bisync()
        self.assertEqual((self.remote / "new local.txt").read_text(), "new local")
        self.assertEqual((self.local / "new remote.txt").read_text(), "new remote")
        self.assertEqual(result.conflicts, 0, self.logs[-1])
        self.assertEqual(result.errors, [], self.logs[-1])
        self.assertEqual((result.to_remote, result.to_local), (1, 1), self.logs[-1])

    def test_normal_dry_run_counts_files_without_double_counting(self):
        for number in range(8):
            self.write(self.local, f"file-{number}.txt", str(number))
        self.initialize()
        self.write(self.local, "nested/new local.txt", "local")
        self.write(self.remote, "nested/new remote.txt", "remote")
        (self.local / "file-0.txt").unlink()
        result = self.run_bisync(dry_run=True)
        self.assertEqual((result.to_remote, result.to_local, result.deletes, result.unknown),
                         (1, 1, 1, 0), self.logs[-1])
        self.assertTrue((self.remote / "file-0.txt").exists())
        self.assertFalse((self.remote / "nested/new local.txt").exists())
        self.assertFalse((self.local / "nested/new remote.txt").exists())

    def test_conflicting_file_count_and_newer_version_wins(self):
        name = "nested/conflict report.txt"
        self.write(self.local, name, "baseline", mtime=1_700_000_000)
        self.initialize()
        self.write(self.local, name, "older local edit", mtime=1_700_000_010)
        self.write(self.remote, name, "newer remote edit", mtime=1_700_000_020)
        result = self.run_bisync()
        self.assertEqual(result.conflicts, 1, self.logs[-1])
        self.assertEqual(result.resolved_conflicts[0]["path"], name, self.logs[-1])
        self.assertEqual(result.resolved_conflicts[0]["winner"], "remote", self.logs[-1])
        for side in (self.local, self.remote):
            self.assertEqual((side / name).read_text(), "newer remote edit")
            self.assertEqual(sorted(p.name for p in (side / "nested").iterdir()),
                             ["conflict report.txt"])

    def test_max_delete_aborts_before_deleting_other_side(self):
        for number in range(8):
            self.write(self.local, f"file-{number}.txt", str(number))
        self.initialize()
        for number in range(4):
            (self.local / f"file-{number}.txt").unlink()
        result = self.run_bisync(success=False)
        self.assertTrue(result.blocked_delete, self.logs[-1])
        for number in range(8):
            self.assertEqual((self.remote / f"file-{number}.txt").read_text(), str(number))

    def test_excludes_at_any_depth_and_marker_is_included(self):
        self.config["excludes"].append(".drivesync*")
        self.paths.filters.write_text(filters.render(self.config["excludes"]), encoding="utf-8")
        excluded = ("node_modules/{prefix}.txt", "deep/node_modules/{prefix}.txt",
                    ".git/{prefix}", "deep/.git/{prefix}", "{prefix}.tmp", "deep/{prefix}.tmp")
        for side in (self.local, self.remote):
            prefix = "local" if side == self.local else "remote"
            for name in excluded:
                self.write(side, name.format(prefix=prefix), prefix)
            self.write(side, f"{prefix}/included.txt", prefix)
        self.initialize()
        for side in (self.local, self.remote):
            other = "remote" if side == self.local else "local"
            self.assertTrue((side / rclone.CHECK_FILENAME).is_file())
            self.assertTrue((side / other / "included.txt").is_file())
            for name in excluded:
                self.assertFalse((side / name.format(prefix=other)).exists(), name)
        # Access checking also proves the broad exclusion did not hide the marker.
        self.run_bisync()

    def test_symlink_to_root_is_skipped(self):
        (self.local / "root-link").symlink_to("/", target_is_directory=True)
        self.write(self.local, "safe.txt", "safe")
        self.initialize()
        self.run_bisync()
        self.assertTrue((self.local / "root-link").is_symlink())
        self.assertFalse((self.remote / "root-link").exists())
        self.assertEqual(sorted(p.name for p in self.remote.iterdir()),
                         [rclone.CHECK_FILENAME, "safe.txt"])

    def test_missing_access_marker_is_recorded_as_error(self):
        self.write(self.local, "safe.txt", "safe")
        self.initialize()
        (self.remote / rclone.CHECK_FILENAME).unlink()
        result = self.run_bisync(success=False)
        self.assertTrue(result.errors, self.logs[-1])
        self.assertIsNotNone(result.last_error)
        self.assertTrue((self.local / rclone.CHECK_FILENAME).exists())

    def test_cli_bootstraps_missing_remote_directory(self):
        # A named local backend permits config validation without any cloud access.
        self.env["RCLONE_CONFIG_TEST_TYPE"] = "local"
        paths = Paths(Path(self.env["XDG_CONFIG_HOME"]) / "drivesync",
                      Path(self.env["XDG_STATE_HOME"]) / "drivesync",
                      Path(self.env["XDG_DATA_HOME"]) / "drivesync")
        paths.ensure()
        value = dict(self.config, remote="test:", folders=[self.folder])
        config.save(paths, value)
        cwd = self.root / "empty-backend"
        cwd.mkdir()
        remote = cwd / value["root_folder"] / self.folder["remote_name"]
        self.assertFalse(remote.parent.exists())
        self.write(self.local, "recording.txt", "local recording")
        command = [sys.executable, str(ROOT / "bin/drivesync-cli"), "init", self.folder["id"]]
        def run(*args):
            process = subprocess.run(command + list(args), env=self.env, cwd=cwd,
                                     capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            self.assertNotIn("libnotify-WARNING", process.stderr)
            return process.stdout
        output = run("--dry-run")
        self.assertIn("Drive klasörü", output)
        self.assertIn("Drive'a 1, bilgisayara 0 kopya, 0 silme", output)
        self.assertTrue(remote.is_dir())
        self.assertEqual(list(remote.iterdir()), [])
        self.assertFalse((self.local / rclone.CHECK_FILENAME).exists())
        # Exercise mkdir again for real init, with the previously previewed path gone.
        remote.rmdir()
        remote.parent.rmdir()
        run("--yes")
        self.assertEqual((remote / "recording.txt").read_text(), "local recording")
        self.assertTrue((remote / rclone.CHECK_FILENAME).is_file())
        self.assertTrue(config.load(paths)["folders"][0]["initialized"])
