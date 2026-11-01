"""Explicit OAuth setup. Credentials and rclone diagnostics never enter logs."""

import json
from pathlib import Path
import re
import subprocess
import uuid

from . import config, i18n
from .paths import atomic_write
from .prerequisites import check_version, executable, remotes
from .state import execution_lock


def setup(paths, client_json, remote="gdrive", replace=False):
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_. -]*", remote):
        raise ValueError(i18n.REMOTE_UNSAFE)
    try:
        raw = Path(client_json).expanduser().read_text(encoding="utf-8")
        client = json.loads(raw)["installed"]
        client_id, secret = client["client_id"], client["client_secret"]
        if not all(config.clean_text(v) for v in (client_id, secret)):
            raise ValueError
        if not client_id.endswith(".apps.googleusercontent.com"):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError(i18n.CLIENT_INVALID) from None
    with execution_lock(paths):
        settings = config.load(paths)
        binary = executable(settings)
        check_version(binary)
        existing = remote + ":" in remotes(binary)
        if existing and not replace:
            raise ValueError(i18n.REMOTE_EXISTS)
        if existing:
            try:
                result = subprocess.run([binary, "config", "file"], capture_output=True,
                                        text=True, timeout=10, check=True)
                source = Path(result.stdout.strip().splitlines()[-1]).expanduser()
                if not source.is_absolute() or not source.is_file():
                    raise ValueError
                atomic_write(paths.config_dir / f"rclone.conf.backup-{uuid.uuid4().hex}",
                             source.read_text(encoding="utf-8"))
            except (OSError, ValueError, IndexError, subprocess.SubprocessError):
                raise ValueError(i18n.REMOTE_BACKUP_FAILED) from None
        atomic_write(paths.config_dir / "google-client.json", raw)
        # Capture all output: even failed providers can echo supplied credentials.
        try:
            command = [binary, "config", "create", remote, "drive", "client_id=" + client_id,
                       "client_secret=" + secret, "scope=drive"]
            if existing:
                # config create updates existing keys; discard the old token so
                # --replace really reopens consent after revocation/expiry.
                command.append("token=")
            result = subprocess.run(command, capture_output=True, text=True, timeout=600,
                                    stdin=subprocess.DEVNULL)
            if result.returncode:
                raise ValueError(i18n.REMOTE_SETUP_FAILED)
            result = subprocess.run([binary, "about", remote + ":"], capture_output=True,
                                    text=True, timeout=60)
            if result.returncode:
                raise ValueError(i18n.REMOTE_SETUP_FAILED)
        except (OSError, subprocess.SubprocessError):
            raise ValueError(i18n.REMOTE_SETUP_FAILED) from None
    # A custom remote must be usable without editing rclone.conf or config.json.
    with config.edit_lock(paths):
        settings = config.load(paths)
        settings["remote"] = remote + ":"
        config.save(paths, settings)
    print(i18n.REMOTE_SETUP_OK.format(remote=remote))
    return 0
