"""Single-instance GLib daemon with one globally serialized rclone child."""

from datetime import datetime, timedelta, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import signal
import time
from datetime import datetime, timedelta, timezone

from gi.repository import Gio, GLib

from . import config as configuration, filters, i18n, init_flow, rclone, prerequisites, remote_watch
from .dbus_service import Service
from .notify import Notifier
from .paths import Paths
from .queue import SyncQueue
from .watcher import FolderWatchers
from .state import BusyError, StateError, StateStore, execution_lock, folder_lock, now, unlocked_folders

# Retry delays (seconds) after a failed init/resync attempt; one more failure gives up.
INIT_BACKOFF = (60, 300, 900, 3600)
INIT_MAX_ATTEMPTS = len(INIT_BACKOFF) + 1
INIT_OFFLINE_WAIT = 60
INIT_SCAN_SECONDS = 5


class Daemon:
    def __init__(self, paths):
        self.paths = paths
        self.config = configuration.load(paths)
        self.loop = GLib.MainLoop()
        self.queue = SyncQueue()
        self.previous_statuses = {}
        self.service = None
        self.store = StateStore(paths, self._changed)
        self.notifier = Notifier()
        self.child = None
        self.lock = None
        self.folder_lock = None
        self.active_folder = None
        self.log = None
        self.progress = None
        self.stopping = False
        self.pending_reload = False
        self.config_valid = True
        self.reconciled = False
        self.offline_since = None
        self.next_setup_check = 0
        self.next_poll = time.monotonic()
        self.next_init_scan = 0
        self.job = None
        self.digests = {}
        self.sources = []
        self.checker = remote_watch.RemoteChecker(paths, lambda: self.config)
        self.remote_warned = False
        self.watchers = FolderWatchers(self.enqueue, GLib.timeout_add, GLib.source_remove)

    def _changed(self):
        if self.service:
            self.service.changed()

    def status_json(self):
        value = self.store.read()
        value["paused"] = self.config["paused"]
        value["folder_config"] = [
            {**{key: folder[key] for key in ("id", "local", "remote_name", "enabled", "initialized")},
             "remote_path": configuration.remote_path(self.config, folder)}
            for folder in self.config["folders"]]
        return json.dumps(value, ensure_ascii=False)

    def _idle_status(self):
        if not self.config_valid:
            return "error"
        if self.config["paused"]:
            return "paused"
        if self.offline_since is not None:
            return "offline"
        items = self.store.read()["folders"]
        if any(items.get(f["id"], {}).get("status") in ("error", "blocked_delete")
               or items.get(f["id"], {}).get("needs_resync")
               for f in self.config["folders"] if f["enabled"]):
            return "error"
        return "idle"

    def _eligible(self, folder):
        item = self.store.read()["folders"].get(folder["id"], {})
        return (self.config_valid and not self.config["paused"] and folder["enabled"]
                and folder["initialized"] and not item.get("needs_resync", False)
                and not item.get("init_request")
                and item.get("status") not in ("blocked_delete", "syncing"))

    def _clear_queue(self):
        for folder_id in self.queue.clear():
            previous = self.previous_statuses.pop(folder_id)
            item = self.store.read()["folders"].get(folder_id, {})
            if item.get("status") == "queued":
                self.store.folder(folder_id, status=previous)

    def reload_config(self):
        try:
            candidate = configuration.load(self.paths)
            if self.child or self.queue.current:
                self.pending_reload = True
                return True
            with execution_lock(self.paths):
                filters.prepare(self.paths, candidate, self.store)
                if not self.reconciled:
                    # Recover interrupted runs only when their folder lock is free.
                    def recover(value):
                        for folder_id, item in value["folders"].items():
                            if folder_id not in available:
                                continue
                            if item["status"] in ("syncing", "queued", "init_queued"):
                                if item.get("init_request"):
                                    # An approved init/resync survives reboots: resume it.
                                    item.update(status="init_queued", last_error=None, progress=None)
                                else:
                                    item.update(status="error", last_error=i18n.INTERRUPTED, progress=None)
                    with unlocked_folders(self.paths, self.store.read()["folders"]) as available:
                        self.store.update(recover)
                    self.reconciled = True
            if candidate["poll_interval_minutes"] != self.config["poll_interval_minutes"]:
                self.next_poll = min(self.next_poll, time.monotonic() + candidate["poll_interval_minutes"] * 60)
            self.config = candidate
            prerequisites.check(candidate)
            self.watchers.configure(candidate)
            self.config_valid = True
            self.pending_reload = False
            fields = {} if self.offline_since is not None else {"last_error": None}
            self.store.daemon(status=self._idle_status(), **fields)
            self._changed()
            self._scan_inits()
            for folder in self.config["folders"]:
                item = self.store.read()["folders"][folder["id"]]
                if item.get("needs_resync"):
                    self.notifier.folder(folder["id"], folder["remote_name"], item)
            return True
        except BusyError:
            self.pending_reload = True
            return True
        except (configuration.ConfigError, StateError, OSError, prerequisites.SetupError) as error:
            self.config_valid = False
            self.next_setup_check = time.monotonic() + 30
            self.pending_reload = False
            self._clear_queue()
            self.store.daemon(status="error", last_error=str(error))
            return False

    def pause(self, paused):
        try:
            # Do not overwrite an external edit with a stale in-memory config.
            with configuration.edit_lock(self.paths):
                candidate = configuration.load(self.paths)
                candidate["paused"] = paused
                configuration.save(self.paths, candidate)
            self.config["paused"] = paused
            if paused:
                self._clear_queue()
            self.pending_reload = True
            self.store.daemon(status="syncing" if self.child else self._idle_status())
            self._changed()
            return True
        except (configuration.ConfigError, OSError):
            return False

    def sync_now(self, folder_id=""):
        if self.stopping or self.pending_reload or not self.config_valid or self.config["paused"]:
            return False
        folders = [f for f in self.config["folders"] if not folder_id or f["id"] == folder_id]
        eligible = [f for f in folders if self._eligible(f)]
        for folder in eligible:
            self.enqueue(folder["id"], "manual")
        return bool(eligible)

    def enqueue(self, folder_id, reason):
        if self.stopping:
            return False
        try:
            folder = configuration.folder_by_id(self.config, folder_id)
        except configuration.ConfigError:
            return False
        try:
            with folder_lock(self.paths, folder_id):
                if not self._eligible(folder):
                    return False
                if self.queue.enqueue(folder_id, reason):
                    self.previous_statuses[folder_id] = self.store.read()["folders"][folder_id]["status"]
                    self.store.folder(folder_id, status="queued")
        except BusyError:
            return False
        return True

    def start_init(self, folder_id, kind):
        """D-Bus StartInit: run a request the CLI/GUI has already persisted."""
        if self.stopping or not self.config_valid or kind not in ("init", "resync"):
            return False
        try:
            folder = configuration.folder_by_id(self.config, folder_id)
            item = self.store.read()["folders"].get(folder_id, {})
        except (configuration.ConfigError, StateError):
            return False
        request = item.get("init_request")
        if (not folder["enabled"] or not isinstance(request, dict) or request.get("kind") != kind
                or item.get("status") == "syncing"):
            return False
        if not self.config["paused"] and not self.pending_reload:
            self._enqueue_init(folder_id)
        # While paused or reloading, the periodic scan queues it afterwards.
        return True

    def _init_ready(self, folder, item):
        request = item.get("init_request")
        if (not self.config_valid or self.config["paused"] or not folder["enabled"]
                or not isinstance(request, dict) or item.get("status") in ("syncing", "needs_approval")
                or item.get("init_attempts", 0) >= INIT_MAX_ATTEMPTS):
            return False
        due = item.get("init_next_retry_at")
        if due:
            try:
                if datetime.fromisoformat(due) > datetime.now(timezone.utc):
                    return False
            except ValueError:
                pass
        return True

    def _enqueue_init(self, folder_id):
        if self.stopping:
            return False
        try:
            folder = configuration.folder_by_id(self.config, folder_id)
            with folder_lock(self.paths, folder_id):
                item = self.store.read()["folders"].get(folder_id, {})
                if not self._init_ready(folder, item):
                    return False
                if self.queue.enqueue(folder_id, "init"):
                    self.previous_statuses[folder_id] = item["status"]
                    self.store.folder(folder_id, status="init_queued")
        except (configuration.ConfigError, BusyError, StateError):
            return False
        return True

    def _scan_inits(self):
        self.next_init_scan = time.monotonic() + INIT_SCAN_SECONDS
        if self.stopping or self.pending_reload or not self.config_valid or self.config["paused"]:
            return
        try:
            items = self.store.read()["folders"]
        except StateError:
            return
        for folder in self.config["folders"]:
            if items.get(folder["id"], {}).get("init_request"):
                self._enqueue_init(folder["id"])

    def _tick(self):
        if self.progress:
            self.progress.poll()
        if self.stopping:
            return GLib.SOURCE_CONTINUE
        if self.pending_reload and not self.child:
            self.reload_config()
        timestamp = time.monotonic()
        if not self.config_valid and not self.child and timestamp >= self.next_setup_check:
            self.reload_config()
        if timestamp >= self.next_init_scan:
            self._scan_inits()
        if timestamp >= self.next_poll:
            self.next_poll = timestamp + self.config["poll_interval_minutes"] * 60
            if not self.pending_reload:
                self._poll()
        if self.offline_since is not None:
            self.notifier.offline(timestamp - self.offline_since)
        self._pump()
        return GLib.SOURCE_CONTINUE

    def _poll(self):
        """Full bisync only when due; otherwise ask Drive cheaply whether anything changed."""
        if self.config["paused"] or self.checker.running:
            return
        current = datetime.now(timezone.utc)
        interval = timedelta(minutes=self.config["full_sync_interval_minutes"])
        items = self.store.read()["folders"]
        cheap, since = [], {}
        for folder in self.config["folders"]:
            if not self._eligible(folder):
                continue
            item = items.get(folder["id"], {})
            last_full = remote_watch.parse_time(item.get("last_full_sync"))
            last_check = remote_watch.parse_time(item.get("last_remote_check"))
            # A failed folder keeps retrying a full bisync on every poll, as before.
            if (last_full is None or last_check is None or current - last_full >= interval
                    or item.get("status") == "error" or item.get("last_exit_code")):
                self.enqueue(folder["id"], "poll")
            else:
                cheap.append(folder)
                since[folder["id"]] = last_check
        if cheap:
            started = current
            self.checker.check(cheap, since, lambda outcome: self._remote_checked(cheap, started, outcome))

    def _remote_checked(self, folders, started, outcome):
        if self.stopping or self.pending_reload:
            return
        if not outcome.ok:
            # Never block syncing: behave as before and run a full bisync.
            if not self.remote_warned:
                self.remote_warned = True
                logging.warning("%s (%s)", i18n.REMOTE_CHANGE_CHECK_FAILED, outcome.error)
            for folder in folders:
                self.enqueue(folder["id"], "poll")
            return
        self.remote_warned = False
        items = self.store.read()["folders"]
        poll_age = timedelta(minutes=self.config["poll_interval_minutes"])
        for folder in folders:
            last_full = remote_watch.parse_time(items.get(folder["id"], {}).get("last_full_sync"))
            loose_due = (folder["id"] in outcome.loose and
                         (last_full is None or started - last_full >= poll_age))
            if folder["id"] in outcome.changed or loose_due:
                self.enqueue(folder["id"], "remote-change")
            else:
                self.store.folder(folder["id"],
                                  last_remote_check=(started - remote_watch.SKEW).isoformat())

    def _pump(self):
        if self.child or self.stopping or self.pending_reload or not self.config_valid or self.config["paused"]:
            return
        if not self.queue.pending:
            return
        try:
            self.lock = execution_lock(self.paths)
            self.lock.__enter__()
        except BusyError:
            self.lock = None
            return
        # Refresh config and state under the lock before selecting any work.
        try:
            candidate = configuration.load(self.paths)
            self.digests = filters.prepare(self.paths, candidate, self.store)
            self.config = candidate
            self.watchers.configure(candidate)
            entry = self.queue.pop()
            folder = next((f for f in self.config["folders"] if f["id"] == entry[0]), None)
            if folder is None:
                self._release()
                return
            if "init" in entry[1]:
                item = self.store.read()["folders"].get(folder["id"], {})
                if not self._init_ready(folder, item):
                    if item.get("status") == "init_queued" and not item.get("init_request"):
                        self.store.folder(folder["id"], status="ok" if folder["initialized"] else "not_initialized")
                    self._release()
                    return
                self.job = dict(kind=item["init_request"].get("kind", "init"),
                                request=item["init_request"])
            elif not self._eligible(folder):
                if self.store.read()["folders"][folder["id"]]["status"] == "queued":
                    self.store.folder(folder["id"], status="ok")
                self._release()
                return
            # The engine lock serializes globally; this documents the active pair
            # for other clients that only need to check a particular folder.
            self.folder_lock = folder_lock(self.paths, folder["id"])
            rclone.check_local(folder)
            self.active_folder = dict(folder)
            self.watchers.begin(folder["id"])
            self.log = rclone.new_log(self.paths, folder["id"])
            self.store.folder(folder["id"], status="syncing", progress=None,
                              last_sync_started=now(), last_log=str(self.log))
            self.store.daemon(status="syncing", current_folder=folder["id"])
            if self.job:
                self._init_begin()
                return
            self.child = rclone.AsyncProcess(rclone.probe_command(self.config), self.log, self._probed)
        except (configuration.ConfigError, StateError, OSError, ValueError) as error:
            folder_id = self.queue.current
            if folder_id and self.job:
                self._init_record_failure(folder_id, str(error))
            elif folder_id:
                self.store.folder(folder_id, status="error", last_error=str(error))
                self._notify(folder_id)
            self._release()

    def _probed(self, code, output):
        self.child = None
        folder_id = self.active_folder["id"]
        if code:
            if rclone.is_network_error(output):
                if self.offline_since is None:
                    self.offline_since = time.monotonic()
                self.store.folder(folder_id, status=self.previous_statuses[folder_id])
                self.store.daemon(last_error=i18n.PROBE_ERROR)
                # One failed probe is enough for this cycle; avoid a burst per folder.
                self._clear_queue()
            else:
                self.offline_since = None
                self.notifier.offline(None)
                self.store.daemon(last_error=None)
                self.store.folder(folder_id, status="error", last_error=i18n.PROBE_ERROR,
                                  last_exit_code=code, last_sync_finished=now())
                self._notify(folder_id)
            self._release()
            return
        self.offline_since = None
        self.notifier.offline(None)
        self.store.daemon(last_error=None)
        if self.stopping:
            self.store.folder(folder_id, status="error", last_error=i18n.INTERRUPTED)
            self._release()
            return
        command = rclone.bisync_command(self.paths, self.config, self.active_folder, self.log)
        self.progress = rclone.LogProgress(self.log, lambda data: self.store.progress(folder_id, data))
        self.child = rclone.AsyncProcess(command, self.log, self._synced)

    def _synced(self, code, output):
        self.child = None
        folder_id = self.active_folder["id"]
        try:
            result = rclone.parse_log(self.log, code)
            rclone.apply_result(self.store, folder_id, result, folder=self.active_folder)
            if code == 0 and not result.blocked_delete:
                # Our own uploads are older than this moment; later remote changes are newer.
                self.store.folder(folder_id, last_full_sync=now(), last_remote_check=now())
                self.checker.mark_stale()
            self._notify(folder_id)
        except (OSError, StateError):
            # A state/log failure must never leave a daemon silently retrying.
            self.config_valid = False
            self.stopping = True
        finally:
            self._release()

    # --- init / resync jobs: approved by the user, executed (and resumed) by the daemon ---

    def _init_begin(self):
        folder, request = self.active_folder, self.job["request"]
        self.job["fingerprint"] = configuration.pair_hash(self.config, folder)
        if request.get("filters_sha256") != self.digests.get(folder["id"]) or request.get("pair_sha256") != self.job["fingerprint"]:
            self._init_needs_approval(i18n.INIT_CONFIG_CHANGED)
            return
        command = [rclone.executable(self.config), "mkdir", configuration.remote_path(self.config, folder)]
        self.child = rclone.AsyncProcess(command, self.log, self._init_mkdir_done)

    def _log_tail(self, path):
        try:
            with open(path, "rb") as stream:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - 65536))
                return stream.read().decode("utf-8", errors="replace")
        except OSError:
            return ""

    def _init_mkdir_done(self, code, output):
        self.child = None
        if code or self.stopping:
            self._init_failed(code, output, self.log)
            return
        self.offline_since = None
        self.notifier.offline(None)
        self.store.daemon(last_error=None)
        folder = self.active_folder
        item = self.store.read()["folders"][folder["id"]]
        if rclone.dry_run_valid(item, self.digests.get(folder["id"]), self.job["fingerprint"]):
            # The approval is fresh and no run has started since: nothing new to check.
            self._init_start_real()
            return
        # Resuming (reboot, logout, retry): the world may have changed since approval.
        self.job["dry_log"] = rclone.new_log(self.paths, folder["id"])
        command = rclone.bisync_command(self.paths, self.config, folder, self.job["dry_log"],
                                       resync=True, dry_run=True)
        self.child = rclone.AsyncProcess(command, self.job["dry_log"], self._init_dry_done)

    def _init_dry_done(self, code, output):
        self.child = None
        log = self.job["dry_log"]
        if code or self.stopping:
            self._init_failed(code, output, log)
            return
        result = rclone.parse_log(log, code, dry_run=True)
        if result.blocked_delete or not init_flow.resume_is_safe(result, self.job["request"]["approved_plan"]):
            self._init_needs_approval(i18n.INIT_NEEDS_APPROVAL)
        else:
            self._init_start_real()

    def _init_start_real(self):
        folder = self.active_folder
        try:
            marker = rclone.create_check(folder)
        except (ValueError, OSError) as error:
            self._init_record_failure(folder["id"], str(error))
            self._release()
            return
        self.store.folder(folder["id"], dry_run_ok_at=None)
        command = [rclone.executable(self.config), "copyto", str(marker),
                   configuration.remote_path(self.config, folder) + "/" + rclone.CHECK_FILENAME,
                   "--skip-links", "--drive-skip-gdocs"]
        self.child = rclone.AsyncProcess(command, self.log, self._init_marker_done)

    def _init_marker_done(self, code, output):
        self.child = None
        if code or self.stopping:
            self._init_failed(code, output, self.log)
            return
        folder = self.active_folder
        command = rclone.bisync_command(self.paths, self.config, folder, self.log, resync=True)
        self.progress = rclone.LogProgress(self.log, lambda data: self.store.progress(folder["id"], data))
        self.child = rclone.AsyncProcess(command, self.log, self._init_synced)

    def _init_synced(self, code, output):
        self.child = None
        folder_id = self.active_folder["id"]
        if code or self.stopping:
            self._init_failed(code, output, self.log)
            return
        try:
            result = rclone.parse_log(self.log, code)
            if result.blocked_delete:
                self._init_needs_approval(i18n.DELETE_BLOCKED)
                return
            try:
                with configuration.edit_lock(self.paths):
                    current = configuration.load(self.paths)
                    current_folder = configuration.folder_by_id(current, folder_id)
                    if (configuration.pair_hash(current, current_folder) != self.job["fingerprint"]
                            or filters.render(configuration.effective_excludes(current, current_folder))
                            != filters.render(configuration.effective_excludes(self.config, self.active_folder))):
                        raise ValueError(i18n.CHANGED_DURING_RUN)
                    current_folder["initialized"] = True
                    configuration.save(self.paths, current)
            except ValueError:
                self._init_needs_approval(i18n.INIT_CONFIG_CHANGED)
                return
            rclone.apply_result(self.store, folder_id, result, folder=self.active_folder)
            self.store.folder(folder_id, init_request=None, init_attempts=0, init_next_retry_at=None)
            self._notify(folder_id)
            self._release()
        except (OSError, StateError, configuration.ConfigError):
            self.config_valid = False
            self.stopping = True
            self._release()

    def _init_failed(self, code, output, log):
        folder_id = self.active_folder["id"]
        if self.stopping:
            # Interrupted by shutdown/logout: the approval stays, the next start resumes.
            self.store.folder(folder_id, status="init_queued", last_error=None)
            self._release()
            return
        if rclone.is_network_error(output + self._log_tail(log)):
            if self.offline_since is None:
                self.offline_since = time.monotonic()
            self.store.daemon(last_error=i18n.PROBE_ERROR)
            # Waiting for the network does not use up a retry.
            self.store.folder(folder_id, status="init_queued", last_error=i18n.PROBE_ERROR,
                              init_next_retry_at=self._later(INIT_OFFLINE_WAIT))
            self._release()
            return
        result = rclone.parse_log(log, code)
        self._init_record_failure(folder_id, result.last_error or i18n.SYNC_ERROR, code)
        self._release()

    def _later(self, seconds):
        return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()

    def _init_record_failure(self, folder_id, message, code=None):
        """Bounded retries: backoff 1, 5, 15, 60 minutes, then give up and ask the user."""
        folder = next((f for f in self.config["folders"] if f["id"] == folder_id),
                      {"initialized": False, "remote_name": folder_id})
        item = self.store.read()["folders"].get(folder_id, {})
        attempts = item.get("init_attempts", 0) + 1
        common = dict(last_error=message, last_exit_code=code, last_sync_finished=now())
        if attempts >= INIT_MAX_ATTEMPTS:
            self.store.folder(folder_id, status="error" if folder["initialized"] else "not_initialized",
                              init_request=None, init_attempts=0, init_next_retry_at=None, **common)
            self.notifier.init_failed(folder["remote_name"], i18n.INIT_GAVE_UP)
        else:
            self.store.folder(folder_id, status="init_queued", init_attempts=attempts,
                              init_next_retry_at=self._later(INIT_BACKOFF[attempts - 1]), **common)

    def _init_needs_approval(self, message):
        folder = self.active_folder
        self.store.folder(folder["id"], status="needs_approval", last_error=message,
                          last_sync_finished=now(), dry_run_ok_at=None, init_next_retry_at=None)
        self.notifier.init_needs_approval(folder["remote_name"])
        self._release()

    def _notify(self, folder_id):
        folder = configuration.folder_by_id(self.config, folder_id)
        self.notifier.folder(folder_id, folder["remote_name"], self.store.read()["folders"][folder_id])

    def _release(self):
        if self.active_folder:
            self.store.folder(self.active_folder["id"], progress=None)
            self.watchers.end(self.active_folder["id"])
        self.progress = None
        self.job = None
        self.previous_statuses.pop(self.queue.current, None)
        self.queue.finish()
        self.active_folder = None
        if self.folder_lock:
            self.folder_lock.close()
            self.folder_lock = None
        if self.lock:
            self.lock.__exit__(None, None, None)
            self.lock = None
        self.store.daemon(current_folder=None, status=self._idle_status())
        if self.stopping:
            self.loop.quit()

    def _config_changed(self, *_):
        self.pending_reload = True

    def _state_changed(self, monitor, file, other, event):
        # CLI init/resync writes through its own StateStore, outside our callback.
        # Watch the directory because atomic writes replace the state inode.
        if any(target and target.get_path() == str(self.paths.state) for target in (file, other)):
            self._changed()

    def shutdown(self):
        if not self.stopping:
            self.stopping = True
            self.checker.cancel()
            self._clear_queue()
            if self.child:
                self.sources.append(GLib.timeout_add_seconds(60, self._terminate))
            else:
                self.loop.quit()
        return GLib.SOURCE_CONTINUE

    def _terminate(self):
        if self.child:
            self.child.terminate()
            self.sources.append(GLib.timeout_add_seconds(5, self._kill))
        return GLib.SOURCE_REMOVE

    def _kill(self):
        if self.child:
            self.child.kill()
        return GLib.SOURCE_REMOVE

    def run(self):
        self.service = Service(self)
        self.store.daemon(pid=os.getpid(), started_at=now(), current_folder=None)
        self.reload_config()
        self.monitor = Gio.File.new_for_path(str(self.paths.config_dir)).monitor_directory(
            Gio.FileMonitorFlags.NONE, None)
        self.monitor.connect("changed", self._config_changed)
        self.state_monitor = Gio.File.new_for_path(str(self.paths.state_dir)).monitor_directory(
            Gio.FileMonitorFlags.NONE, None)
        self.state_monitor.connect("changed", self._state_changed)
        self.sources.append(GLib.timeout_add(250, self._tick))
        for signum in (signal.SIGINT, signal.SIGTERM):
            self.sources.append(GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, self.shutdown))
        try:
            self.loop.run()
        finally:
            self.watchers.close()
            self.monitor.cancel()
            self.state_monitor.cancel()
            for source in self.sources:
                if GLib.MainContext.default().find_source_by_id(source):
                    GLib.source_remove(source)
            self.store.daemon(pid=None, current_folder=None, status=self._idle_status())
            self.service.close()


def main():
    paths = Paths.from_env()
    paths.ensure()
    handler = RotatingFileHandler(paths.logs / "daemon.log", maxBytes=5 * 1024 * 1024,
                                  backupCount=3, encoding="utf-8")
    logging.basicConfig(handlers=[handler], level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        Daemon(paths).run()
        return 0
    except (configuration.ConfigError, StateError, RuntimeError, GLib.Error, OSError) as error:
        text = str(error) if isinstance(error, (configuration.ConfigError, StateError, RuntimeError)) else i18n.DAEMON_FAILED
        print(text, file=__import__("sys").stderr)
        logging.error("%s", text)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
