"""Run inside dbus-run-session; only temporary paths and fake rclone are used."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from gi.repository import GLib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from isolation_guard import require_isolated_xdg
require_isolated_xdg()
from support import ROOT
from drivesync import config
from drivesync.dbus_service import BUS_NAME, INTERFACE, OBJECT_PATH
from drivesync.paths import Paths


def wait_for(check, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except (subprocess.CalledProcessError, ValueError):
            pass
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for session-bus state")


def call(method, *arguments):
    result = subprocess.run(["gdbus", "call", "--session", "--dest", BUS_NAME,
                             "--object-path", OBJECT_PATH, "--method", INTERFACE + "." + method,
                             *arguments], text=True, capture_output=True, timeout=6, check=True)
    return GLib.Variant.parse(None, result.stdout, None, None).unpack()[0]


def status():
    return json.loads(call("GetStatus"))


def main():
    paths = Paths.from_env()
    # The parent test prepared a valid initialized baseline with fake rclone.
    root = Path(os.environ["FAKE_RCLONE_ROOT"]).resolve()
    for name in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
                 "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
        assert Path(os.environ[name]).resolve().is_relative_to(root), name
    assert paths.state.resolve().is_relative_to(root)
    assert config.load(paths)["rclone_path"] == str(ROOT / "tests/fixtures/fake_rclone.py")
    if sys.argv[1:] == ["--check-environment"]:
        print("Isolated environment and checkout imports: OK")
        return
    daemon = subprocess.Popen([sys.executable, str(ROOT / "bin/drivesync-daemon")],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        value = wait_for(status)
        assert value["daemon"]["pid"] == daemon.pid
        assert value["folder_config"][0]["id"] == "docs"
        xml = subprocess.check_output(["gdbus", "introspect", "--session", "--dest", BUS_NAME,
                                       "--object-path", OBJECT_PATH, "--xml"], text=True, timeout=6)
        interface = ET.fromstring(xml).find(f"interface[@name='{INTERFACE}']")
        assert {m.attrib["name"] for m in interface.findall("method")} == {
            "GetStatus", "SyncNow", "StartInit", "Pause", "Resume", "ReloadConfig"}
        assert interface.find("signal").attrib["name"] == "StatusChanged"
        duplicate = subprocess.run([sys.executable, str(ROOT / "bin/drivesync-daemon")],
                                   capture_output=True, text=True, timeout=6)
        assert duplicate.returncode != 0
        assert status()["daemon"]["pid"] == daemon.pid
        wait_for(lambda: status()["folders"]["docs"]["last_success"])
        assert call("Pause") is True
        assert status()["paused"] is True
        assert config.load(paths)["paused"] is True
        assert call("SyncNow", "docs") is False
        assert call("Resume") is True
        assert call("ReloadConfig") is True
        wait_for(lambda: not status()["paused"])
        assert call("SyncNow", "docs") is True
        wait_for(lambda: status()["folders"]["docs"]["status"] == "syncing")
        blocked = subprocess.run([sys.executable, str(ROOT / "bin/drivesync-cli"),
                                  "init", "docs", "--dry-run"], text=True,
                                 capture_output=True, timeout=6)
        assert blocked.returncode == 1
        wait_for(lambda: status()["folders"]["docs"]["status"] == "ok")
        value = config.load(paths)
        value["excludes"].append("*.smoke")
        config.save(paths, value)
        wait_for(lambda: status()["folders"]["docs"]["needs_resync"])
        assert call("SyncNow", "docs") is False
        print("gdbus GetStatus, API, single instance, pause/resume, queue, lock and reload: OK")
    finally:
        daemon.terminate()
        try:
            out, err = daemon.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            daemon.kill()
            out, err = daemon.communicate()
            raise AssertionError("Daemon did not shut down")
        if daemon.returncode != 0:
            raise AssertionError(f"Daemon failed: {out}\n{err}")


if __name__ == "__main__":
    main()
