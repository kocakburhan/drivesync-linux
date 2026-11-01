"""Local-only rclone checks. Never probe Drive or expose backend diagnostics."""

import os
import re
import subprocess

from . import i18n


def executable(config):
    return os.path.expanduser(config["rclone_path"])


class SetupError(ValueError):
    pass


def check_version(binary):
    try:
        result = subprocess.run([binary, "version"], capture_output=True, text=True, timeout=10)
        match = re.match(r"rclone v(\d+)\.(\d+)(?:\.(\d+))?\b", result.stdout)
        if result.returncode or not match or tuple(map(int, match.group(1, 2))) < (1, 66):
            raise SetupError(i18n.RCLONE_REQUIRED)
    except (OSError, subprocess.TimeoutExpired):
        raise SetupError(i18n.RCLONE_REQUIRED) from None


def remotes(binary):
    try:
        result = subprocess.run([binary, "listremotes"], capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise SetupError(i18n.REMOTE_CHECK_FAILED)
        return set(result.stdout.splitlines())
    except (OSError, subprocess.TimeoutExpired):
        raise SetupError(i18n.REMOTE_CHECK_FAILED) from None


def check(config):
    binary = executable(config)
    check_version(binary)
    if config["remote"] not in remotes(binary):
        raise SetupError(i18n.REMOTE_MISSING)
