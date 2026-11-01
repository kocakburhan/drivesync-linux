"""Release check (at most once a day, no auth) and helpers for the one-command update."""

from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request

from . import __version__, i18n

REPO = "kocakburhan/drivesync-linux"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASE_PAGE = f"https://github.com/{REPO}/releases/latest"
CHECK_INTERVAL = timedelta(hours=24)
TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 1024 * 1024
UPDATE_UNIT_PREFIX = "drivesync-update"

_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


class UpdateError(Exception):
    pass


def parse_version(text):
    """Return (major, minor, patch) for "vX.Y.Z" or "X.Y.Z"; anything else is None."""
    if not isinstance(text, str):
        return None
    match = _VERSION.fullmatch(text.strip())
    return tuple(int(part) for part in match.groups()) if match else None


def normalize(text):
    version = parse_version(text)
    return None if version is None else ".".join(str(part) for part in version)


def is_newer(latest, current):
    """True only when both versions parse and latest is strictly greater (numeric, not text)."""
    newest, installed = parse_version(latest), parse_version(current)
    return newest is not None and installed is not None and newest > installed


def fetch_latest(current=__version__, opener=urllib.request.urlopen):
    """Ask GitHub for the newest published release. Raises UpdateError on any failure."""
    request = urllib.request.Request(LATEST_API, headers={
        "User-Agent": f"DriveSync/{current}", "Accept": "application/vnd.github+json"})
    try:
        with opener(request, timeout=TIMEOUT_SECONDS) as response:
            data = json.loads(response.read(MAX_RESPONSE_BYTES).decode("utf-8"))
    except (OSError, ValueError) as error:
        raise UpdateError(type(error).__name__) from None
    version = normalize(data.get("tag_name")) if isinstance(data, dict) else None
    if version is None:
        raise UpdateError("tag_name")
    url = data.get("html_url")
    if not isinstance(url, str) or not url.startswith("https://github.com/"):
        url = RELEASE_PAGE
    return dict(latest=version, url=url)


def parse_time(text):
    try:
        value = datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None
    if value is not None and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


class UpdateChecker:
    """Throttle, record and announce release checks.

    Main-thread methods (due, begin, finish) touch the state file; ``fetch`` is the only
    network call and may run in a worker thread.
    """

    def __init__(self, store, notify, *, clock=None, fetch=None, current=__version__):
        self.store = store
        self.notify = notify
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.fetch = fetch or (lambda: fetch_latest(current))
        self.current = current
        self.failure_logged = False

    def due(self, enabled):
        if not enabled:
            return False
        checked = parse_time(self.store.read()["update"].get("checked_at"))
        return checked is None or self.clock() - checked >= CHECK_INTERVAL

    def begin(self):
        """Record the attempt before the request, so failures and restarts still wait a day."""
        stamp = self.clock().isoformat(timespec="seconds")
        self.store.update(lambda value: value["update"].update(checked_at=stamp))

    def finish(self, outcome):
        """Apply a fetch result (a dict, or the exception the fetch raised)."""
        if isinstance(outcome, Exception):
            if not self.failure_logged:
                self.failure_logged = True
                logging.warning("%s (%s)", i18n.UPDATE_CHECK_FAILED, outcome)
            return
        self.failure_logged = False
        latest, url = outcome["latest"], outcome["url"]
        notified = self.store.read()["update"].get("notified")

        def record(value):
            value["update"].update(latest=latest, url=url)
        self.store.update(record)
        if is_newer(latest, self.current) and notified != latest:
            def announce(value):
                value["update"]["notified"] = latest
            self.store.update(announce)
            self.notify(latest, url, start_update)


def cli_path():
    sibling = Path(sys.argv[0]).resolve().with_name("drivesync-cli")
    if sibling.is_file():
        return str(sibling)
    return shutil.which("drivesync-cli")


def start_update():
    """Run ``drivesync-cli update --yes`` in a transient user unit.

    The daemon restarts itself during the update, so the job must not live in its cgroup.
    """
    cli = cli_path()
    if cli is None:
        logging.warning("%s", i18n.UPDATE_NO_CLI)
        return False
    unit = f"{UPDATE_UNIT_PREFIX}-{int(time.time())}"
    command = ["systemd-run", "--user", "--collect", "--quiet", f"--unit={unit}", cli, "update", "--yes"]
    try:
        subprocess.run(command, check=True, timeout=10, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        logging.warning("%s", i18n.UPDATE_START_FAILED)
        return False
    return True


def install_info_path():
    """install.sh writes lib/drivesync/install-info.json next to the installed package."""
    return Path(__file__).resolve().parents[1] / "install-info.json"


def read_install_info(path=None):
    path = path or install_info_path()
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError(i18n.UPDATE_NO_INFO) from None
    if (not isinstance(value, dict) or not isinstance(value.get("source_dir"), str)
            or not Path(value["source_dir"]).is_absolute()):
        raise ValueError(i18n.UPDATE_NO_INFO)
    return value
