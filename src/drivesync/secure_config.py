"""Opt-in encryption of the rclone configuration (it holds the Drive OAuth token).

rclone encrypts rclone.conf with a random password. The password lives in the desktop keyring
(Secret Service) and reaches rclone only through ``RCLONE_PASSWORD_COMMAND``, which runs
``drivesync-cli keyring-password`` (a helper, so both keyring backends behave the same and a TTY
never receives the secret). The password never appears on a command line, in a log or in config.json.
"""

from datetime import datetime
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile

from . import config as configuration, i18n, keyring_store
from .prerequisites import check_version, executable
from .state import execution_lock

ENV_NAME = "RCLONE_PASSWORD_COMMAND"
BACKUP_PREFIX = "rclone.conf.before-encryption-"


def quote_argument(value):
    """rclone splits the command like a space-separated CSV record: quote with double quotes."""
    if value and not any(c in value for c in ' \t"'):
        return value
    return '"' + value.replace('"', '""') + '"'


def helper_launcher():
    """The drivesync-cli launcher that belongs to the running code (installed prefix or checkout)."""
    here = Path(__file__).resolve()
    for root in (here.parents[2], here.parents[3]):
        candidate = root / "bin" / "drivesync-cli"
        if candidate.is_file():
            return str(candidate)
    home_cli = Path.home() / ".local/bin/drivesync-cli"
    if home_cli.is_file():
        return str(home_cli)
    raise ValueError(i18n.SECURE_NO_HELPER)


def password_command():
    return quote_argument(helper_launcher()) + " keyring-password"


def rclone_env(config, base=None):
    """Environment for every rclone child. The single place that adds the password command."""
    env = dict(os.environ if base is None else base)
    command = (config or {}).get("rclone_password_command")
    if command:
        env[ENV_NAME] = command
    return env


def enabled(config):
    return bool(config.get("rclone_password_command"))


def gate(config):
    """None when rclone may run; "locked" or "missing" when the configured password is unreadable."""
    if not enabled(config):
        return None
    store = keyring_store.backend()
    if store is None:
        return "locked"
    try:
        store.lookup()
    except keyring_store.SecretMissing:
        return "missing"
    except keyring_store.KeyringError:
        return "locked"
    return None


def _run(binary, args, env, timeout=60):
    return subprocess.run([binary, *args], env=env, capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL)


def encryption_state(binary, env):
    """True encrypted, False plaintext, None when unknown (needs a password we do not have)."""
    try:
        result = _run(binary, ["config", "encryption", "check"], env, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 2 and "NOT encrypted" in result.stderr:
        return False
    return None


def config_file(binary, env):
    try:
        result = _run(binary, ["config", "file"], env, timeout=10)
        path = Path(result.stdout.strip().splitlines()[-1]).expanduser()
    except (OSError, IndexError, subprocess.SubprocessError):
        raise ValueError(i18n.SECURE_NO_CONFIG) from None
    if result.returncode or not path.is_absolute() or not path.is_file():
        raise ValueError(i18n.SECURE_NO_CONFIG)
    return path


def _remotes(binary, env):
    result = _run(binary, ["listremotes"], env, timeout=20)
    if result.returncode:
        raise ValueError(i18n.REMOTE_CHECK_FAILED)
    return set(result.stdout.splitlines())


def _write_private(path, data):
    """Write bytes to a new 0600 file next to ``path`` and move it into place atomically."""
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _save_command(paths, command):
    with configuration.edit_lock(paths):
        settings = configuration.load(paths)
        settings["rclone_password_command"] = command
        configuration.save(paths, settings)


def status(paths):
    settings = configuration.load(paths, create=False)
    store = keyring_store.backend()
    env = rclone_env(settings)
    binary = executable(settings)
    encrypted = encryption_state(binary, env) if Path(binary).exists() else None
    keyring = keyring_store.probe(store)
    return dict(enabled=enabled(settings), encrypted=encrypted,
                backend=store.name if store else None, keyring=keyring,
                password_command=bool(settings["rclone_password_command"]))


def enable(paths):
    with execution_lock(paths):
        settings = configuration.load(paths)
        if enabled(settings):
            raise ValueError(i18n.SECURE_ALREADY)
        binary = executable(settings)
        check_version(binary)
        plain_env = rclone_env(settings)
        state = encryption_state(binary, plain_env)
        if state is True:
            raise ValueError(i18n.SECURE_ALREADY)
        if state is None:
            raise ValueError(i18n.SECURE_UNSUPPORTED)
        store = keyring_store.backend()
        if store is None:
            raise ValueError(i18n.SECURE_NO_BACKEND)
        keyring = keyring_store.probe(store)
        if keyring != "ok":
            raise ValueError(i18n.SECURE_KEYRING_LOCKED if keyring == "locked" else i18n.SECURE_NO_BACKEND)
        source = config_file(binary, plain_env).resolve()
        before = _remotes(binary, plain_env)
        command = password_command()
        original = source.read_bytes()
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        backup = paths.config_dir / (BACKUP_PREFIX + stamp)
        paths.config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(original)
        stored = False
        try:
            store.store(secrets.token_urlsafe(32))
            stored = True
            env = dict(plain_env, **{ENV_NAME: command})
            result = _run(binary, ["config", "encryption", "set"], env)
            if result.returncode:
                raise ValueError(i18n.SECURE_FAILED)
            if (encryption_state(binary, env) is not True or _remotes(binary, env) != before
                    or b"RCLONE_ENCRYPT_V0" not in source.read_bytes()[:200]):
                raise ValueError(i18n.SECURE_FAILED)
            _save_command(paths, command)
        except BaseException as error:
            try:
                _write_private(source, original)
                if stored:
                    store.delete()
            except (OSError, keyring_store.KeyringError):
                raise ValueError(i18n.SECURE_ROLLBACK_FAILED.format(path=backup)) from None
            backup.unlink(missing_ok=True)
            if isinstance(error, (OSError, subprocess.SubprocessError, keyring_store.KeyringError)):
                raise ValueError(i18n.SECURE_FAILED) from None
            raise
        return dict(enabled=True, backup=str(backup))


def disable(paths):
    with execution_lock(paths):
        settings = configuration.load(paths)
        if not enabled(settings):
            raise ValueError(i18n.SECURE_NOT_ENABLED)
        binary = executable(settings)
        check_version(binary)
        env = rclone_env(settings)
        store = keyring_store.backend()
        if store is None or gate(settings) is not None:
            raise ValueError(i18n.SECURE_KEYRING_LOCKED)
        if encryption_state(binary, env) is True:
            if _run(binary, ["config", "encryption", "remove"], env).returncode:
                raise ValueError(i18n.SECURE_FAILED)
        plain = rclone_env(dict(settings, rclone_password_command=""))
        plain.pop(ENV_NAME, None)
        if encryption_state(binary, plain) is not False:
            raise ValueError(i18n.SECURE_FAILED)
        _save_command(paths, "")
        try:
            store.delete()
        except keyring_store.KeyringError:
            raise ValueError(i18n.SECURE_DELETE_FAILED) from None
        return dict(enabled=False)


def main_helper(stdout=None, stderr=None):
    """``drivesync-cli keyring-password``: print the password for rclone. Exit 1 on any failure."""
    stdout, stderr = stdout or sys.stdout, stderr or sys.stderr
    if stdout.isatty():
        print(i18n.KEYRING_PASSWORD_TTY, file=stderr)
        return 1
    store = keyring_store.backend()
    try:
        if store is None:
            raise keyring_store.KeyringUnavailable("no backend")
        password = store.lookup()
    except keyring_store.SecretMissing:
        print(i18n.KEYRING_MISSING, file=stderr)
        return 1
    except keyring_store.KeyringError:
        print(i18n.KEYRING_LOCKED, file=stderr)
        return 1
    stdout.write(password + "\n")
    stdout.flush()
    return 0
