#!/usr/bin/env python3
"""Offline rclone double. Refuses non-test remotes and non-isolated paths."""

import fcntl
import json
import os
from pathlib import Path
import sys
import time


def main():
    root = Path(os.environ["FAKE_RCLONE_ROOT"]).resolve()
    for variable in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        assert Path(os.environ[variable]).resolve().is_relative_to(root)
    argv = sys.argv[1:]
    command = argv[0]
    # Local metadata checks deliberately do not enter the sync-call record.
    settings_path = root / "fake-settings.json"
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    if command in ("version", "listremotes"):
        options = settings.get(command, {})
        print(options.get("output", "rclone v1.70.0" if command == "version" else "test:"))
        return options.get("code", 0)
    assert command in ("bisync", "mkdir", "copyto", "lsf", "lsjson", "backend")
    if command == "bisync":
        assert Path(argv[1]).resolve().is_relative_to(root)
        assert argv[2].startswith("test:")
    elif command == "copyto":
        assert Path(argv[1]).resolve().is_relative_to(root)
        assert argv[2].startswith("test:")
    elif command == "backend":
        assert argv[1] == "query" and argv[2].startswith("test:")
    else:
        assert argv[1].startswith("test:")

    def record(phase):
        with open(os.environ["FAKE_RCLONE_RECORD"], "a", encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            stream.write(json.dumps({"argv": argv, "phase": phase, "pid": os.getpid(),
                                     "time": time.monotonic()}) + "\n")

    record("start")
    settings_path = root / "fake-settings.json"
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    options = settings.get(command, {})
    if command == "backend":
        options = settings.get("query", {})
    if command == "bisync" and "--dry-run" in argv:
        options = settings.get("dry_run", options)
    elif command == "bisync" and "--resync" in argv:
        options = settings.get("resync", options)  # real init/resync runs, distinct from routine syncs
    code = options.get("code", 0)
    remote_markers = root / "remote-markers.json"
    markers = json.loads(remote_markers.read_text()) if remote_markers.exists() else []
    if command == "copyto" and code == 0:
        markers.append(argv[2])
        remote_markers.write_text(json.dumps(markers))
    if command == "bisync":
        log = Path(argv[argv.index("--log-file") + 1])
        assert log.resolve().is_relative_to(root)
        if settings.get("strict_check_access") and "--check-access" in argv:
            if not (Path(argv[1]) / ".drivesync-check").is_file() or argv[2] + "/.drivesync-check" not in markers:
                options = {"code": 7, "log": "ERROR : access check failed\n"}
                code = 7
        if "--dry-run" in argv:
            default = ("INFO : - Path2 Queue copy to Path1 - remote.txt\n"
                       "INFO : - Path1 Queue copy to Path2 - local.txt\n"
                       "INFO : - Path2 Queue delete - obsolete.txt\n")
        else:
            default = "INFO : Transferred: 1 / 1, 100%\nINFO : Bisync successful\n"
        with log.open("a", encoding="utf-8") as stream:
            stream.write(options.get("log", default))
            stream.flush()
            time.sleep(options.get("sleep", 0))
            stream.write(options.get("end_log", ""))
    else:
        print(options.get("output", ""), flush=True)
        time.sleep(options.get("sleep", 0))
    record("finish")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
