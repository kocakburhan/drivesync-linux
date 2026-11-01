"""Secret Service access for the rclone configuration password.

Two backends, same small interface: GI ``Secret`` (libsecret, gir1.2-secret-1) when importable,
otherwise the ``secret-tool`` CLI (libsecret-tools). Nothing here prompts the user on lookup: a locked
keyring is reported as ``KeyringLocked`` so the daemon can wait. The password is never put on a
command line and never logged.
"""

import shutil
import subprocess

APPLICATION = "drivesync"
PURPOSE = "rclone-config"
SCHEMA_NAME = "io.github.kocakburhan.DriveSync.RcloneConfig"
LABEL = "DriveSync rclone configuration password"
ATTRIBUTES = {"application": APPLICATION, "purpose": PURPOSE}


class KeyringError(Exception):
    pass


class KeyringUnavailable(KeyringError):
    """No Secret Service, no backend, or a failed call."""


class KeyringLocked(KeyringError):
    """The keyring exists but is locked (typically before the first desktop login unlock)."""


class SecretMissing(KeyringError):
    """The keyring is readable but holds no DriveSync item."""


class LibsecretBackend:
    name = "libsecret"

    def __init__(self):
        import gi
        gi.require_version("Secret", "1")
        from gi.repository import GLib, Secret
        self.GLib, self.Secret = GLib, Secret
        self.schema = Secret.Schema.new(SCHEMA_NAME, Secret.SchemaFlags.NONE,
                                        {key: Secret.SchemaAttributeType.STRING for key in ATTRIBUTES})

    def _service(self):
        try:
            return self.Secret.Service.get_sync(self.Secret.ServiceFlags.LOAD_COLLECTIONS, None)
        except self.GLib.Error:
            raise KeyringUnavailable("secret service unreachable") from None

    def state(self):
        """Raise unless the default collection is unlocked. Never prompts."""
        service = self._service()
        try:
            collection = self.Secret.Collection.for_alias_sync(
                service, self.Secret.COLLECTION_DEFAULT, self.Secret.CollectionFlags.NONE, None)
        except self.GLib.Error:
            raise KeyringUnavailable("no default collection") from None
        if collection is None:
            raise KeyringUnavailable("no default collection")
        if collection.get_locked():
            raise KeyringLocked("keyring locked")

    def lookup(self):
        service = self._service()
        try:
            items = service.search_sync(self.schema, dict(ATTRIBUTES),
                                        self.Secret.SearchFlags.ALL | self.Secret.SearchFlags.LOAD_SECRETS, None)
        except self.GLib.Error:
            raise KeyringUnavailable("search failed") from None
        if not items:
            self.state()  # distinguishes "locked/unavailable" from "really absent"
            raise SecretMissing("no item")
        for item in items:
            if item.get_locked():
                continue
            value = item.get_secret()
            if value is not None and value.get_text():
                return value.get_text()
        raise KeyringLocked("keyring locked")

    def store(self, password):
        self.state()
        try:
            self.Secret.password_store_sync(self.schema, dict(ATTRIBUTES), self.Secret.COLLECTION_DEFAULT,
                                            LABEL, password, None)
        except self.GLib.Error:
            raise KeyringUnavailable("store failed") from None

    def delete(self):
        try:
            self.Secret.password_clear_sync(self.schema, dict(ATTRIBUTES), None)
        except self.GLib.Error:
            raise KeyringUnavailable("delete failed") from None


class SecretToolBackend:
    name = "secret-tool"

    def __init__(self):
        self.binary = shutil.which("secret-tool")
        if not self.binary:
            raise ImportError("secret-tool")
        self.args = [item for pair in ATTRIBUTES.items() for item in pair]

    def _run(self, *args, input_text=None):
        try:
            return subprocess.run([self.binary, *args], input=input_text, capture_output=True,
                                  text=True, timeout=30, stdin=None if input_text else subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            raise KeyringUnavailable("secret-tool failed") from None

    def state(self):
        self.lookup_or_none()

    def lookup_or_none(self):
        result = self._run("lookup", *self.args)
        if result.returncode == 0 and result.stdout:
            return result.stdout.rstrip("\n")
        # secret-tool exits 1 with no output for "not found", and with a diagnostic when the
        # service is unreachable or the collection could not be unlocked.
        if result.returncode == 1 and not result.stderr.strip():
            return None
        raise KeyringLocked("keyring locked or unavailable")

    def lookup(self):
        value = self.lookup_or_none()
        if value is None:
            raise SecretMissing("no item")
        return value

    def store(self, password):
        result = self._run("store", "--label=" + LABEL, *self.args, input_text=password)
        if result.returncode:
            raise KeyringUnavailable("store failed")

    def delete(self):
        result = self._run("clear", *self.args)
        if result.returncode not in (0, 1):
            raise KeyringUnavailable("delete failed")


_override = []


def set_backend(backend):
    """Tests install a fake store here; pass None to restore detection."""
    _override[:] = [backend] if backend is not None else []


def backend():
    """The usable backend, or None when neither libsecret nor secret-tool exists."""
    if _override:
        return _override[0]
    for factory in (LibsecretBackend, SecretToolBackend):
        try:
            return factory()
        except (ImportError, ValueError, AttributeError):
            continue
    return None


def probe(store=None):
    """One of "ok", "locked", "unavailable"; "ok" means a read/write would not prompt."""
    store = store or backend()
    if store is None:
        return "unavailable"
    try:
        store.state()
    except KeyringLocked:
        return "locked"
    except KeyringError:
        return "unavailable"
    return "ok"
