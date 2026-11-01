"""Argument construction, asynchronous child execution and bounded log parsing."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import signal
import uuid

from gi.repository import Gio, GLib

from . import i18n
from .config import remote_path, pair_hash
from .state import folder_state, now
from .progress import LogProgress, parse_progress

CHECK_FILENAME = ".drivesync-check"


def executable(config):
    return os.path.expanduser(config["rclone_path"])


def bisync_command(paths, config, folder, log, *, resync=False, dry_run=False, force=False):
    command = [executable(config), "bisync", os.path.expanduser(folder["local"]),
               remote_path(config, folder), "--workdir", str(paths.workdir / folder["id"]),
               "--filters-file", str(paths.filters_for(folder["id"])), "--resilient", "--recover", "--max-lock", "5m",
               "--conflict-resolve", "newer", "--conflict-loser", "delete", "--check-access",
               "--check-filename", CHECK_FILENAME, "--max-delete", str(config["max_delete_percent"]),
               "--drive-skip-gdocs", "--skip-links", "--create-empty-src-dirs", "--compare", "size,modtime",
               "--log-file", str(log), "--log-level", "INFO", "--stats", "2s", "--stats-one-line"]
    initial_run = resync and not dry_run
    command += ["--transfers", str(config["init_transfers"] if initial_run else config["transfers"]),
                "--checkers", str(config["checkers"])]
    if initial_run:
        command += config["init_extra_flags"]
    if resync:
        command += ["--resync", "--resync-mode", "newer"]
    if dry_run:
        command.append("--dry-run")
        if resync and not folder["initialized"]:
            # rclone enforces access markers even on dry resync. Bootstrap
            # simulation precedes marker creation and never mutates user data.
            command.remove("--check-access")
    if force:
        command.append("--force")
    return command


def probe_command(config):
    return [executable(config), "lsf", remote_path(config), "--max-depth", "1", "--dirs-only",
            "--contimeout", "15s", "--timeout", "30s", "--retries", "1", "--low-level-retries", "1"]


def new_log(paths, folder_id):
    paths.ensure()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    path = paths.logs / f"{stamp}-{folder_id}-{uuid.uuid4().hex[:8]}.log"
    path.touch(mode=0o600)
    logs = sorted(paths.logs.glob("*-*.log"))
    for old in logs[:-200]:
        old.unlink(missing_ok=True)
    return path


def redact(text):
    # Backend diagnostics can include authentication failures; do not expose credentials.
    text = re.sub(r"(?i)Bearer\s+\S+", "Bearer [REDACTED]", text)
    text = re.sub(r'(?i)(access_token|refresh_token|client_secret|authorization|token)'
                  r'(["\s]*[:=]\s*["\s]*)([^\s,"}]+)', r'\1\2[REDACTED]', text)
    return text


def error_summary(text, limit=80):
    """One readable diagnostic for narrow CLI tables; full errors stay in state/logs."""
    lines = text.splitlines()
    line = next((line for line in lines if re.match(
        r"^(?:\d{4}/\d{2}/\d{2} \S+ )?(?:ERROR|CRITICAL)\s*:", line)),
        lines[0] if lines else "")
    line = re.sub(r"^\d{4}/\d{2}/\d{2} \S+\s+", "", line)
    line = re.sub(r"^(?:ERROR|CRITICAL)\s*:\s*", "", line)
    line = " ".join(redact(line).split())
    return line if len(line) <= limit else line[:limit - 1] + "…"


@dataclass
class RunResult:
    exit_code: int
    errors: list = field(default_factory=list)
    conflicts: int = 0
    blocked_delete: bool = False
    to_remote: int = 0
    to_local: int = 0
    deletes: int = 0
    unknown: int = 0
    actions: list = field(default_factory=list)
    resolved_conflicts: list = field(default_factory=list)

    @property
    def last_error(self):
        return "\n".join(self.errors)[:500] or None


def parse_log(path, exit_code, *, dry_run=False):
    result = RunResult(exit_code)
    direction = None
    conflicting_paths = set()
    conflict_details = {}
    queued = {"local": 0, "remote": 0}
    skipped = {"local": 0, "remote": 0}
    queued_deletes = skipped_deletes = 0
    with open(path, encoding="utf-8", errors="replace") as stream:
        for line in stream:
            raw = line.rstrip("\r\n")
            text = redact(raw)
            # Read the message separately so filenames containing words like
            # "conflict", "ERROR" or "too many deletes" cannot change status.
            header = re.match(r"^(?:\d{4}/\d{2}/\d{2} \S+ )?"
                              r"(DEBUG|INFO|NOTICE|ERROR|CRITICAL)(?:\s*:\s*|\s+)(.*)$",
                              raw)
            message = header[2] if header else raw
            if header and header[1] in ("ERROR", "CRITICAL") and len(result.errors) < 3:
                result.errors.append(text[:500])
            conflict = re.match(r"^- WARNING\s+New or changed in both paths\s+- (.+)$", message)
            if conflict:
                conflicting_paths.add(conflict[1])
                stamp = re.match(r"^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})", raw)
                try:
                    timestamp = (datetime.strptime(stamp[1], "%Y/%m/%d %H:%M:%S")
                                 .astimezone().isoformat() if stamp else now())
                except ValueError:
                    timestamp = now()
                conflict_details.setdefault(conflict[1], dict(path=redact(conflict[1]),
                                                             time=timestamp, winner=None))
            winner = re.match(r"^(.+): The winner is: (Path[12])\s*$", message)
            if winner and winner[1] in conflict_details:
                conflict_details[winner[1]]["winner"] = "local" if winner[2] == "Path1" else "remote"
            if re.match(r"^(?:Safety abort: too many deletes(?:\s*\(|$)|Failed to bisync: too many deletes$)",
                        message, re.IGNORECASE):
                result.blocked_delete = True
            copying = re.match(r"^(?:- Path[12]\s+(?:Resync is copying files to|Do queued copies to)"
                                r"\s+- |Copying Path[12] files to )(Path[12])\b", message)
            if copying:
                direction = "local" if copying[1] == "Path1" else "remote"
            action = False
            copy = re.match(r"^(?:- Path[12]\s+)?Queue copy to (Path[12])\s+- (.+)$", message)
            if copy:
                queued["local" if copy[1] == "Path1" else "remote"] += 1
                action = True
            elif re.match(r"^(?:- Path[12]\s+)?Queue delete\s+- ", message):
                queued_deletes += 1
                action = True
            elif re.match(r"^.+: Skipped copy as --dry-run is set\b", message):
                if direction:
                    skipped[direction] += 1
                else:
                    result.unknown += 1
                action = True
            elif re.match(r"^.+: Skipped delete as --dry-run is set\b", message):
                skipped_deletes += 1
                action = True
            # mkdir, directory modtime and filter-hash notices are bookkeeping,
            # not file copies or deletions and not unknown file actions.
            if action and len(result.actions) < 50:
                result.actions.append(text)
    result.conflicts = len(conflicting_paths)
    # A warning in a failed run or preview is not proof of resolution.
    if exit_code == 0 and not result.blocked_delete and not dry_run:
        result.resolved_conflicts = list(conflict_details.values())[-100:]
    # Normal bisync queues each file before logging its dry-run skip. Resync
    # logs only skips, so use them only when that direction has no queue.
    result.to_remote = queued["remote"] or skipped["remote"]
    result.to_local = queued["local"] or skipped["local"]
    result.deletes = queued_deletes or skipped_deletes
    return result


def is_network_error(text):
    lower = text.lower()
    return any(term in lower for term in (
        "network is unreachable", "network unreachable", "no route to host", "no such host",
        "temporary failure in name resolution", "connection refused", "connection reset",
        "connection timed out", "i/o timeout", "context deadline exceeded", "tls handshake timeout",
        "dial tcp", "server misbehaving", "unexpected eof"))


class AsyncProcess:
    """Drain stdout incrementally, and reap before invoking completion exactly once."""

    def __init__(self, command, log, done):
        self.log = Path(log)
        self.done = done
        self.output = ""
        self.eof = False
        self.exited = False
        self.finished = False
        self.exit_code = -1
        self.process = None
        try:
            self.process = Gio.Subprocess.new(command, Gio.SubprocessFlags.STDOUT_PIPE |
                                             Gio.SubprocessFlags.STDERR_MERGE)
            self.stream = self.process.get_stdout_pipe()
            self.buffer = b""
            self.stream.read_bytes_async(65536, GLib.PRIORITY_DEFAULT, None, self._read)
            self.process.wait_async(None, self._exited)
        except GLib.Error:
            self._append(i18n.SYNC_ERROR)
            self.eof = self.exited = True
            GLib.idle_add(self._complete)

    def _append(self, line):
        line = redact(line)
        self.output = (self.output + line + "\n")[-65536:]
        with self.log.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    def _read(self, stream, result):
        try:
            data = stream.read_bytes_finish(result).get_data()
            if not data:
                if self.buffer:
                    self._append(self.buffer.decode("utf-8", errors="replace"))
                self.eof = True
                self._complete()
                return
            lines = (self.buffer + data).split(b"\n")
            self.buffer = lines.pop()
            for line in lines:
                self._append(line.decode("utf-8", errors="replace"))
            stream.read_bytes_async(65536, GLib.PRIORITY_DEFAULT, None, self._read)
        except GLib.Error:
            self.eof = True
            self._complete()

    def _exited(self, process, result):
        process.wait_finish(result)
        self.exit_code = process.get_exit_status() if process.get_if_exited() else -process.get_term_sig()
        self.exited = True
        self._complete()

    def _complete(self):
        if self.eof and self.exited and not self.finished:
            self.finished = True
            self.done(self.exit_code, self.output)
        return GLib.SOURCE_REMOVE

    def terminate(self):
        if self.process and not self.exited:
            self.process.send_signal(signal.SIGTERM)

    def kill(self):
        if self.process and not self.exited:
            self.process.force_exit()


def run_sync(command, log, progress=None, *, state_progress=None):
    """CLI uses a local GLib loop; stats written by rclone are tailed live."""
    loop = GLib.MainLoop()
    outcome = []
    offset = 0
    tracker = LogProgress(log, state_progress) if state_progress else None

    def tail():
        nonlocal offset
        if tracker:
            tracker.poll()
        if progress:
            with open(log, encoding="utf-8", errors="replace") as stream:
                stream.seek(offset)
                for line in stream:
                    progress(redact(line.rstrip()))
                offset = stream.tell()
        return GLib.SOURCE_CONTINUE

    def finished(code, output):
        outcome.append((code, output))
        loop.quit()

    child = AsyncProcess(command, log, finished)
    timer = GLib.timeout_add(200, tail)
    try:
        loop.run()
    except KeyboardInterrupt:
        child.terminate()
        # Keep the execution lock until the interrupted child has actually exited.
        killer = GLib.timeout_add_seconds(5, lambda: (child.kill(), GLib.SOURCE_REMOVE)[1])
        loop.run()
        if GLib.MainContext.default().find_source_by_id(killer):
            GLib.source_remove(killer)
        raise
    finally:
        GLib.source_remove(timer)
        tail()
    return outcome[0]


def check_local(folder):
    local = Path(os.path.expanduser(folder["local"]))
    if local.is_symlink() or not local.is_dir():
        raise ValueError(i18n.LOCAL_UNSAFE)
    return local


def create_check(folder):
    local = check_local(folder)
    marker = local / CHECK_FILENAME
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        if marker.is_symlink() or not marker.is_file():
            raise ValueError(i18n.CHECK_UNSAFE) from None
    else:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(i18n.CHECK_TEXT)
    return marker


def dry_run_valid(item, digest, fingerprint):
    try:
        timestamp = datetime.fromisoformat(item["dry_run_ok_at"])
        age = datetime.now(timezone.utc) - timestamp
        return (timedelta(0) <= age < timedelta(hours=24) and
                item.get("dry_run_filters_sha256") == digest and
                item.get("dry_run_pair_sha256") == fingerprint)
    except (KeyError, TypeError, ValueError):
        return False


def apply_result(store, folder_id, result, *, initialized=True, folder=None):
    fields = {"last_sync_finished": now(), "last_exit_code": result.exit_code,
              "conflicts": result.conflicts, "last_error": result.last_error, "progress": None}
    if not initialized and (result.exit_code or result.blocked_delete):
        fields.update(status="not_initialized", needs_resync=False,
                      last_error=result.last_error or (i18n.DELETE_BLOCKED if result.blocked_delete
                                                       else i18n.SYNC_ERROR))
    elif result.blocked_delete:
        fields.update(status="blocked_delete", last_error=i18n.DELETE_BLOCKED)
        # A delete safety abort alone can be retried explicitly with --force.
        # Never clear an existing resync requirement after an unsuccessful resync.
    elif result.exit_code == 7:
        fields.update(status="error", needs_resync=True,
                      last_error=result.last_error or i18n.RESYNC_REQUIRED)
    elif result.exit_code:
        fields.update(status="error", last_error=result.last_error or i18n.SYNC_ERROR)
    else:
        fields.update(status="ok" if initialized else "not_initialized", needs_resync=False,
                      last_success=now(), last_error=None)
    def mutate(value):
        value["folders"].setdefault(folder_id, folder_state()).update(fields)
        if result.resolved_conflicts and result.exit_code == 0 and not result.blocked_delete:
            metadata = folder or {}
            entries = [dict(item, folder=folder_id, folder_name=metadata.get("remote_name", folder_id),
                            folder_local=os.path.expanduser(metadata.get("local", "")))
                       for item in result.resolved_conflicts]
            value["recent_conflicts"] = (value["recent_conflicts"] + entries)[-100:]
    return store.update(mutate)
