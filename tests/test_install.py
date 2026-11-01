import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

from support import ROOT


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="drivesync-install-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1")
        for name, directory in (("XDG_CONFIG_HOME", "config"), ("XDG_STATE_HOME", "state"),
                                ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache")):
            self.env[name] = str(self.home / directory)
        for name in ("DRIVESYNC_PREFIX", "DRIVESYNC_SYSTEMD_DIR"):
            self.env.pop(name, None)
        fake_bin = self.root / "fake-bin"
        fake_bin.mkdir()
        for name, contents in (("rclone", "echo rclone v1.66.0"),
                               ("systemctl", 'echo systemctl-called >&2; exit 99'),
                               ("update-desktop-database", 'echo desktop-cache >> "$HOME/cache-calls"; exit 99'),
                               ("gtk-update-icon-cache", 'echo icon-cache >> "$HOME/cache-calls"; exit 99')):
            binary = fake_bin / name
            binary.write_text("#!/bin/sh\n" + contents + "\n")
            binary.chmod(0o755)
        self.env["PATH"] = str(fake_bin) + os.pathsep + os.environ["PATH"]
        self.config = self.home / "config/drivesync/config.json"
        self.units = self.home / "config/systemd/user"
        self.share = self.home / "data"

    def run_script(self, script, *args):
        result = subprocess.run([str(ROOT / script), "--no-enable", *args], env=self.env,
                                text=True, capture_output=True, timeout=20, cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("systemctl-called", result.stderr)
        return result

    def check_install(self, prefix):
        for name in ("daemon", "tray", "cli", "app"):
            path = prefix / f"bin/drivesync-{name}"
            self.assertTrue(path.is_file())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)
        for name in ("tray", "presentation", "config", "dbus_service", "nautilus_ext", "nautilus_model"):
            path = prefix / f"lib/drivesync/drivesync/{name}.py"
            self.assertTrue(path.is_file())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        self.assertFalse(list(prefix.rglob("__pycache__")))
        style = prefix / "lib/drivesync/drivesync/style.css"
        self.assertEqual(style.read_text(), (ROOT / "data/style.css").read_text())
        self.assertEqual(stat.S_IMODE(style.stat().st_mode), 0o644)
        desktop = self.share / "applications/io.github.kocakburhan.DriveSync.desktop"
        self.assertTrue(desktop.is_file())
        for field in ("Exec=drivesync-app", "Icon=io.github.kocakburhan.DriveSync",
                      "StartupWMClass=io.github.kocakburhan.DriveSync.App",
                      "Categories=Utility;Network;FileTransfer;"):
            self.assertIn(field, desktop.read_text())
        extension = self.share / "nautilus-python/extensions/drivesync.py"
        self.assertTrue(extension.is_file())
        # Reproduce nautilus-python's import name, with no checkout on sys.path.
        result = subprocess.run(["python3", "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); import drivesync; "
            "assert drivesync.DriveSyncExtension; import drivesync.nautilus_model",
            str(extension.parent)], env=self.env, cwd=self.root,
            text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        for relative in ("scalable/apps/io.github.kocakburhan.DriveSync.svg",
                         *(f"scalable/emblems/emblem-drivesync-{name}.svg" for name in ("ok", "sync", "error")),
                         *(f"scalable/status/drivesync-tray-{name}.svg"
                           for name in ("ok", "syncing", "paused", "error", "offline", "idle",
                                        *(f"syncing-{frame}" for frame in range(8)))),
                         *(f"symbolic/apps/drivesync-{name}-symbolic.svg"
                           for name in ("ok", "syncing", "paused", "error", "offline", "idle",
                                        *(f"syncing-{frame}" for frame in range(8))))):
            icon = self.share / "icons/hicolor" / relative
            self.assertTrue(icon.is_file(), relative)
            self.assertEqual(stat.S_IMODE(icon.stat().st_mode), 0o644)
        calls = (self.home / "cache-calls").read_text()
        self.assertIn("desktop-cache", calls)
        self.assertIn("icon-cache", calls)
        for name in ("drivesync.service", "drivesync-tray.service"):
            unit = self.units / name
            self.assertEqual(stat.S_IMODE(unit.stat().st_mode), 0o644)
            self.assertIn("Environment=PYTHONDONTWRITEBYTECODE=1", unit.read_text())
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        config = json.loads(self.config.read_text())
        self.assertEqual(config["folders"], [])
        self.assertTrue(all(not f["initialized"] for f in config["folders"]))
        result = subprocess.run([str(prefix / "bin/drivesync-cli"), "config-path"],
                                env=self.env, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(self.config))

    def test_default_install_twice_uninstall_keeps_config_and_purge(self):
        prefix = self.home / ".local"
        result = self.run_script("install.sh")
        self.assertIn("nautilus -q", result.stdout)
        self.check_install(prefix)
        self.assertIn('ExecStart="%h/.local/bin/drivesync-daemon"',
                      (self.units / "drivesync.service").read_text())
        config = json.loads(self.config.read_text())
        config["custom_extension"] = "koru"
        self.config.write_text(json.dumps(config))
        before = self.config.read_bytes()
        self.run_script("install.sh")
        self.assertEqual(self.config.read_bytes(), before)
        for base in ("state", "data"):
            directory = self.home / base / "drivesync"
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "sentinel").write_text("keep")
        other_icon = self.share / "icons/hicolor/scalable/apps/other-app.svg"
        other_icon.write_text("keep")
        other_status = self.share / "icons/hicolor/scalable/status/other-app.svg"
        other_status.write_text("keep")
        cache_calls_before = (self.home / "cache-calls").read_text().count("icon-cache")
        self.run_script("uninstall.sh")
        self.run_script("uninstall.sh")
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse((prefix / "lib/drivesync").exists())
        for name in ("daemon", "tray", "cli", "app"):
            self.assertFalse((prefix / f"bin/drivesync-{name}").exists())
        self.assertFalse((self.share / "applications/io.github.kocakburhan.DriveSync.desktop").exists())
        self.assertFalse((self.share / "nautilus-python/extensions/drivesync.py").exists())
        self.assertFalse(list((self.share / "icons/hicolor/scalable/emblems").glob("emblem-drivesync-*.svg")))
        self.assertFalse(list((self.share / "icons/hicolor").rglob("*DriveSync.svg")))
        self.assertFalse(list((self.share / "icons/hicolor").rglob("drivesync-*-symbolic.svg")))
        self.assertFalse(list((self.share / "icons/hicolor/scalable/status").glob("drivesync-tray-*.svg")))
        self.assertEqual(other_status.read_text(), "keep")
        self.assertEqual((self.home / "cache-calls").read_text().count("icon-cache"),
                         cache_calls_before + 2)
        self.assertEqual(other_icon.read_text(), "keep")
        self.assertFalse((self.units / "drivesync.service").exists())
        self.assertFalse((self.units / "drivesync-tray.service").exists())
        for base in ("state", "data"):
            self.assertTrue((self.home / base / "drivesync/sentinel").is_file())
        self.run_script("uninstall.sh", "--purge")
        self.run_script("uninstall.sh", "--purge")
        for base in ("config", "state", "data"):
            self.assertFalse((self.home / base / "drivesync").exists())

    def test_prefix_and_environment_overrides(self):
        prefix = self.home / "custom prefix"
        self.env["DRIVESYNC_PREFIX"] = "unused"
        self.units = self.root / "custom units"
        self.env["DRIVESYNC_SYSTEMD_DIR"] = str(self.units)
        self.run_script("install.sh", "--prefix", "custom prefix")
        self.check_install(prefix)
        self.assertIn('ExecStart="%h/custom prefix/bin/drivesync-tray"',
                      (self.units / "drivesync-tray.service").read_text())
        self.assertFalse((self.home / "unused").exists())
        self.run_script("uninstall.sh", "--prefix", "custom prefix", "--purge")
        self.assertFalse((prefix / "lib/drivesync").exists())
        self.env["DRIVESYNC_PREFIX"] = str(self.root / "env-prefix")
        self.run_script("install.sh")
        self.check_install(self.root / "env-prefix")
        self.run_script("uninstall.sh", "--purge")

    def test_unit_contract(self):
        daemon = (ROOT / "data/systemd/drivesync.service").read_text()
        tray = (ROOT / "data/systemd/drivesync-tray.service").read_text()
        for field in ("WantedBy=default.target", "Restart=on-failure", "RestartSec=30"):
            self.assertIn(field, daemon)
        for field in ("PartOf=graphical-session.target", "WantedBy=graphical-session.target",
                      "After=drivesync.service"):
            self.assertIn(field, tray)

    def test_old_rclone_fails_before_install_and_prints_both_install_methods(self):
        binary = self.root / "fake-bin/rclone"
        binary.write_text("#!/bin/sh\necho rclone v1.65.2\n")
        result = subprocess.run([str(ROOT / "install.sh"), "--no-enable"], env=self.env,
                                text=True, capture_output=True, timeout=20, cwd=self.root)
        self.assertEqual(result.returncode, 1)
        combined = result.stdout + result.stderr
        self.assertIn("1.66", combined)
        self.assertIn("./install.sh --with-rclone", combined)
        self.assertIn("sudo -v ; curl https://rclone.org/install.sh | sudo bash", combined)
        self.assertNotIn("Traceback", combined)
        self.assertFalse(self.config.exists())
        self.assertFalse((self.home / ".local/lib/drivesync").exists())

    def test_with_rclone_routes_to_local_installer_and_uses_new_binary(self):
        # Stub only the download module, never perform a real request. Its ZIP /
        # checksum implementation is exercised separately with in-memory fakes.
        python = self.root / "fake-bin/python3"
        python.write_text('''#!/bin/bash
if [[ $1 = -m && $2 = drivesync.install_rclone ]]; then
    mkdir -p "$HOME/.local/bin"
    printf '#!/bin/sh\\necho rclone v1.70.0\\n' > "$HOME/.local/bin/rclone"
    chmod 755 "$HOME/.local/bin/rclone"
    echo fake-local-installer
    exit 0
fi
exec /usr/bin/python3 "$@"
''')
        python.chmod(0o755)
        old = self.root / "fake-bin/rclone"
        old.write_text("#!/bin/sh\necho rclone v1.65.0\n")
        self.config.parent.mkdir(parents=True)
        self.config.write_text(json.dumps(dict(rclone_path=str(old), root_folder="Keep Root",
                                              custom_extension="keep", folders=[])))
        result = self.run_script("install.sh", "--with-rclone")
        self.assertIn("fake-local-installer", result.stdout)
        installed = json.loads(self.config.read_text())
        self.assertEqual(installed["rclone_path"], str(self.home / ".local/bin/rclone"))
        self.assertEqual(installed["folders"], [])
        self.assertEqual(installed["root_folder"], "Keep Root")
        self.assertEqual(installed["custom_extension"], "keep")
        self.assertEqual(old.read_text(), "#!/bin/sh\necho rclone v1.65.0\n")
