"""XDG paths and private, atomic file writes."""

import json
import os
from pathlib import Path
import tempfile
from dataclasses import dataclass


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


@dataclass(frozen=True)
class Paths:
    config_dir: Path
    state_dir: Path
    data_dir: Path

    @classmethod
    def from_env(cls):
        home = Path.home()
        def base(variable, fallback):
            value = os.environ.get(variable)
            return Path(value) if value and Path(value).is_absolute() else home / fallback
        return cls(base("XDG_CONFIG_HOME", ".config") / "drivesync",
                   base("XDG_STATE_HOME", ".local/state") / "drivesync",
                   base("XDG_DATA_HOME", ".local/share") / "drivesync")

    @property
    def config(self):
        return self.config_dir / "config.json"

    @property
    def state(self):
        return self.state_dir / "state.json"

    @property
    def logs(self):
        return self.state_dir / "logs"

    @property
    def workdir(self):
        return self.data_dir / "bisync"

    @property
    def remote_index(self):
        return self.data_dir / "remote-index"

    @property
    def filters_dir(self):
        return self.data_dir / "filters"

    def filters_for(self, folder_id):
        return self.filters_dir / f"{folder_id}.txt"

    @property
    def legacy_filters(self):
        return self.data_dir / "filters.txt"

    def ensure(self):
        for path in (self.config_dir, self.state_dir, self.data_dir, self.logs, self.workdir):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
