"""User-local rclone installer; stdlib only, no sudo or archive extraction."""

import hashlib
import io
import os
from pathlib import Path
import platform
import re
import tempfile
import urllib.request
import zipfile

from . import i18n

BASE = "https://downloads.rclone.org"


def download(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        payload = response.read(150 * 1024 * 1024 + 1)
    if len(payload) > 150 * 1024 * 1024:
        raise ValueError(i18n.RCLONE_INSTALL_FAILED)
    return payload


def install(*, fetch=download, machine=None, destination=None):
    architecture = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(
        (machine or platform.machine()).lower())
    if architecture is None or platform.system() != "Linux":
        raise ValueError(i18n.RCLONE_ARCH)
    version_text = fetch(BASE + "/version.txt").decode("ascii").strip()
    match = re.fullmatch(r"rclone v(\d+\.\d+\.\d+)", version_text)
    if not match or tuple(map(int, match[1].split("."))) < (1, 66, 0):
        raise ValueError(i18n.RCLONE_INSTALL_FAILED)
    version = match[1]
    archive = f"rclone-v{version}-linux-{architecture}.zip"
    release = f"{BASE}/v{version}/"
    checksums = fetch(release + "SHA256SUMS").decode("ascii")
    matches = [m[1].lower() for line in checksums.splitlines()
               if (m := re.fullmatch(r"([a-fA-F0-9]{64})\s+\*?(.+)", line)) and m[2] == archive]
    if len(matches) != 1:
        raise ValueError(i18n.RCLONE_CHECKSUM)
    payload = fetch(release + archive)
    if hashlib.sha256(payload).hexdigest() != matches[0]:
        raise ValueError(i18n.RCLONE_CHECKSUM)
    with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
        member = f"rclone-v{version}-linux-{architecture}/rclone"
        info = bundle.getinfo(member)
        if info.file_size > 150 * 1024 * 1024:
            raise ValueError(i18n.RCLONE_INSTALL_FAILED)
        binary = bundle.read(info)
    target = destination or Path.home() / ".local/bin/rclone"
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".rclone-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(binary)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o755)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target


def main():
    try:
        print(i18n.RCLONE_INSTALLED.format(path=install()))
        return 0
    except (OSError, ValueError, KeyError, UnicodeError, zipfile.BadZipFile):
        print(i18n.RCLONE_INSTALL_FAILED)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
