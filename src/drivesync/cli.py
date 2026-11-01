"""Bilingual CLI, with direct, locked initialization and explicit safety overrides."""

import argparse
import json
import hashlib
import os
import subprocess
from pathlib import Path
import sys

from . import __version__, app_model, config as configuration, filters, i18n, init_flow, prerequisites

# doctor must work before python3-gi and the typelibs have been installed.
try:
    from gi.repository import GLib
    from . import dbus_service, rclone
except (ImportError, ValueError):
    GLib = dbus_service = rclone = None
BUS_ERRORS = (GLib.Error,) if GLib is not None else ()
from .setup_remote import setup
from .notify import Notifier
from .paths import Paths
from .presentation import progress_text
from .state import BusyError, StateError, StateStore, execution_lock, folder_lock, folder_state, now


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs["add_help"] = False
        super().__init__(*args, **kwargs)
        self._positionals.title = i18n.POSITIONAL
        self._optionals.title = i18n.OPTIONS
        self.add_argument("-h", "--help", action="help", help=i18n.HELP_OPTION)

    def format_usage(self):
        return super().format_usage().replace("usage: ", i18n.USAGE, 1)

    def format_help(self):
        return super().format_help().replace("usage: ", i18n.USAGE, 1)

    def error(self, message):
        self.print_usage(sys.stderr)
        # argparse diagnostics are kept out of the Turkish product interface.
        self.exit(2, i18n.ARG_ERROR.format(program=self.prog, message=i18n.REQUEST_REFUSED))


def parser():
    root = Parser(description=i18n.CLI_DESCRIPTION)
    root.add_argument("--version", action="version", version=__version__, help=i18n.VERSION_HELP)
    commands = root.add_subparsers(dest="command", required=True, title=i18n.COMMAND)
    for command, help_text in i18n.HELP.items():
        child = commands.add_parser(command, help=help_text, description=help_text)
        if command in ("sync", "logs", "init", "resync", "force-sync"):
            child.add_argument("id", nargs="?" if command in ("sync", "logs") else None,
                               help=i18n.FOLDER_HELP)
        if command in ("init", "resync"):
            child.add_argument("--dry-run", action="store_true", help=i18n.DRY_HELP)
            child.add_argument("--json", action="store_true", help=i18n.JSON_HELP)
        if command == "setup-remote":
            child.add_argument("--client-json", required=True, help=i18n.CLIENT_JSON_HELP)
            child.add_argument("--remote", default="gdrive", help=i18n.REMOTE_HELP)
            child.add_argument("--replace", action="store_true", help=i18n.REPLACE_HELP)
        if command == "add-folder":
            child.add_argument("local", help=i18n.ADD_LOCAL_HELP)
            child.add_argument("--name", help=i18n.ADD_NAME_HELP)
        if command in ("init", "resync", "force-sync", "setup-remote"):
            child.add_argument("--yes", action="store_true", help=i18n.YES_HELP)
        if command in ("init", "resync"):
            child.add_argument("--background", action="store_true", help=i18n.BACKGROUND_HELP)
            child.add_argument("--foreground", action="store_true", help=i18n.FOREGROUND_HELP)
        if command in ("status", "doctor", "progress", "add-folder"):
            child.add_argument("--json", action="store_true", help=i18n.MACHINE_JSON_HELP)
        if command == "doctor":
            child.add_argument("--offline", action="store_true", help=i18n.OFFLINE_HELP)
        if command == "progress":
            child.add_argument("id", help=i18n.FOLDER_HELP)
        if command == "folder":
            actions = child.add_subparsers(dest="action", required=True, title=i18n.COMMAND)
            for action in ("list", "add", "remove", "enable", "disable"):
                sub = actions.add_parser(action, description=i18n.HELP["folder"])
                sub.add_argument("--json", action="store_true", help=i18n.MACHINE_JSON_HELP)
                if action == "add":
                    sub.add_argument("local", help=i18n.ADD_LOCAL_HELP)
                    sub.add_argument("--remote-name", dest="name", help=i18n.ADD_NAME_HELP)
                elif action != "list":
                    sub.add_argument("id", help=i18n.FOLDER_HELP)
                if action == "remove":
                    sub.add_argument("--yes", action="store_true", help=i18n.YES_HELP)
            excludes = actions.add_parser("excludes", description=i18n.HELP["folder"])
            excludes.add_argument("id", help=i18n.FOLDER_HELP)
            edits = excludes.add_subparsers(dest="exclude_action", required=True, title=i18n.COMMAND)
            for action in ("list", "add", "remove"):
                sub = edits.add_parser(action, description=i18n.HELP["folder"])
                sub.add_argument("--json", action="store_true", help=i18n.MACHINE_JSON_HELP)
                if action != "list":
                    sub.add_argument("pattern", help=i18n.EXCLUDE_HELP)
        if command == "config":
            actions = child.add_subparsers(dest="action", required=True, title=i18n.COMMAND)
            for action in ("get", "set"):
                sub = actions.add_parser(action, description=i18n.HELP["config"])
                sub.add_argument("key", choices=SAFE_KEYS, help=i18n.CONFIG_KEY_HELP)
                if action == "set":
                    sub.add_argument("value", help=i18n.CONFIG_VALUE_HELP)
                sub.add_argument("--json", action="store_true", help=i18n.MACHINE_JSON_HELP)
            excludes = actions.add_parser("excludes", description=i18n.HELP["config"])
            edits = excludes.add_subparsers(dest="exclude_action", required=True, title=i18n.COMMAND)
            for action in ("list", "add", "remove"):
                sub = edits.add_parser(action, description=i18n.HELP["config"])
                sub.add_argument("--json", action="store_true", help=i18n.MACHINE_JSON_HELP)
                if action != "list":
                    sub.add_argument("pattern", help=i18n.EXCLUDE_HELP)
    return root


def status(paths):
    try:
        return json.loads(dbus_service.call("GetStatus"))
    except BUS_ERRORS:
        config = configuration.load(paths, create=False)
        value = StateStore(paths).read()
        value["paused"] = config["paused"]
        value["folder_config"] = config["folders"]
        value["daemon"]["status"] = "error"
        value["daemon"]["last_error"] = i18n.NO_DAEMON
        try:
            prerequisites.check(config)
        except prerequisites.SetupError as error:
            value["daemon"]["last_error"] = str(error)
        return value


def print_status(value):
    print(i18n.DAEMON_STATUS.format(status=i18n.STATUSES.get(value["daemon"]["status"], i18n.STATUSES["error"]),
                                   paused=i18n.YES if value["paused"] else i18n.NO))
    if value["daemon"].get("last_error"):
        print(value["daemon"]["last_error"])
    print(i18n.HEADERS)
    for folder in value["folder_config"]:
        item = value["folders"].get(folder["id"], {})
        name = item.get("status", "not_initialized")
        if not folder["enabled"]:
            name = "disabled"
        print(f'{folder["id"]:<22} {i18n.STATUSES.get(name, i18n.STATUSES["error"]):<24} '
              f'{item.get("last_success") or i18n.NEVER}')
        if item.get("last_error"):
            print("  " + rclone.error_summary(item["last_error"]))


def _check_unchanged(paths, config, folder, digest):
    current = configuration.load(paths)
    current_folder = configuration.folder_by_id(current, folder["id"])
    if (configuration.pair_hash(current, current_folder) != configuration.pair_hash(config, folder)
            or filters.render(configuration.effective_excludes(current, current_folder))
            != filters.render(configuration.effective_excludes(config, folder))):
        raise ValueError(i18n.CHANGED_DURING_RUN)
    return current, current_folder


def direct(paths, folder_id, *, dry_run=False, force=False, resync=False, json_output=False):
    def failed(message):
        if json_output:
            emit(dict(ok=False, error=dict(code="sync_failed", message=rclone.redact(message))))
        print(message, file=sys.stderr)
        return 1

    paths.ensure()
    with execution_lock(paths, folder_id):
        config = configuration.load(paths)
        folder = configuration.folder_by_id(config, folder_id)
        if not folder["enabled"]:
            raise ValueError(i18n.DISABLED)
        rclone.check_local(folder)
        store = StateStore(paths)
        digest = filters.prepare(paths, config, store, owned_folder_id=folder_id)[folder_id]
        item = store.read()["folders"][folder_id]
        fingerprint = configuration.pair_hash(config, folder)
        if force:
            if not folder["initialized"] or item["needs_resync"]:
                raise ValueError(i18n.FORCE_INELIGIBLE)
            if item["status"] != "blocked_delete":
                raise ValueError(i18n.FORCE_ONLY_BLOCKED)
        elif not dry_run and not rclone.dry_run_valid(item, digest, fingerprint):
            raise ValueError(i18n.DRY_RUN_REQUIRED)

        prerequisites.check(config)
        log = rclone.new_log(paths, folder_id)
        store.folder(folder_id, last_log=str(log), last_sync_started=now(),
                     status="syncing", dry_run_ok_at=None, progress=None)
        notifier = Notifier(actions=False)  # the CLI exits before a button could be clicked
        try:
            if not force:
                command = [rclone.executable(config), "mkdir", configuration.remote_path(config, folder)]
                code, _ = rclone.run_sync(command, log)
                if code:
                    result = rclone.parse_log(log, code)
                    rclone.apply_result(store, folder_id, result, initialized=folder["initialized"], folder=folder)
                    print(i18n.DRY_FAILED if dry_run else i18n.SYNC_ERROR, file=sys.stderr)
                    return failed(result.last_error or i18n.SYNC_ERROR)
                if not json_output:
                    print(i18n.REMOTE_READY)
            if not force and not dry_run:
                marker = rclone.create_check(folder)
                command = [rclone.executable(config), "copyto", str(marker),
                           configuration.remote_path(config, folder) + "/" + rclone.CHECK_FILENAME,
                           "--skip-links", "--drive-skip-gdocs"]
                code, _ = rclone.run_sync(command, log, print)
                if code:
                    rclone.apply_result(store, folder_id, rclone.parse_log(log, code),
                                        initialized=folder["initialized"], folder=folder)
                    notifier.folder(folder_id, folder["remote_name"], store.read()["folders"][folder_id])
                    print(i18n.SYNC_ERROR, file=sys.stderr)
                    return 1
            command = rclone.bisync_command(paths, config, folder, log,
                                           resync=not force, dry_run=dry_run, force=force)
            if dry_run and not folder["initialized"] and not json_output:
                print(i18n.BOOTSTRAP_DRY)
            code, _ = rclone.run_sync(command, log, None if dry_run else print,
                                     state_progress=lambda data: store.progress(folder_id, data))
            result = rclone.parse_log(log, code, dry_run=dry_run)
            if dry_run and code == 0 and not result.blocked_delete:
                _check_unchanged(paths, config, folder, digest)
                if not json_output:
                    print(i18n.DRY_SUMMARY.format(to_remote=result.to_remote, to_local=result.to_local,
                                                deletes=result.deletes))
                    if result.unknown:
                        print(i18n.DRY_UNKNOWN.format(count=result.unknown))
                    print(i18n.DRY_ACTIONS)
                    for line in result.actions:
                        print(line)
                # A pending re-approval stays visible until the user decides.
                keep = folder["initialized"] or item["status"] == "needs_approval"
                store.folder(folder_id, dry_run_ok_at=now(), dry_run_filters_sha256=digest,
                             dry_run_pair_sha256=fingerprint,
                             dry_run_plan=init_flow.approved_plan(
                                 dict(to_remote=result.to_remote, to_local=result.to_local,
                                      deletes=result.deletes)),
                             status=item["status"] if keep else "not_initialized",
                             last_error=item["last_error"] if keep else None,
                             needs_resync=item["needs_resync"] if folder["initialized"] else False,
                             last_exit_code=0, last_sync_finished=now())
                if json_output:
                    emit(dict(to_remote=result.to_remote, to_local=result.to_local,
                              deletes=result.deletes, unknown=result.unknown, actions=result.actions[:50]))
                else:
                    print(i18n.DRY_OK)
                return 0
            elif code == 0 and not result.blocked_delete:
                with configuration.edit_lock(paths):
                    current, current_folder = _check_unchanged(paths, config, folder, digest)
                    current_folder["initialized"] = True
                    configuration.save(paths, current)
                rclone.apply_result(store, folder_id, result, folder=folder)
                store.folder(folder_id, init_request=None, init_attempts=0, init_next_retry_at=None)
                notifier.folder(folder_id, folder["remote_name"], store.read()["folders"][folder_id])
                print(i18n.REQUEST_OK if force else i18n.RESYNC_OK if resync else i18n.INIT_OK)
                return 0
            rclone.apply_result(store, folder_id, result, initialized=folder["initialized"], folder=folder)
            notifier.folder(folder_id, folder["remote_name"], store.read()["folders"][folder_id])
            if dry_run:
                print(i18n.DRY_FAILED, file=sys.stderr)
            return failed(result.last_error or store.read()["folders"][folder_id]["last_error"])
        except KeyboardInterrupt:
            store.folder(folder_id, status="error" if folder["initialized"] else "not_initialized",
                         needs_resync=folder["initialized"], last_error=i18n.INTERRUPTED, last_sync_finished=now())
            raise
        except (ValueError, OSError):
            store.folder(folder_id, status="error" if folder["initialized"] else "not_initialized",
                         last_error=i18n.CHANGED_DURING_RUN,
                         needs_resync=folder["initialized"], last_sync_finished=now())
            raise
        finally:
            store.folder(folder_id, progress=None)


SAFE_KEYS = ("root_folder", "language", "poll_interval_minutes", "full_sync_interval_minutes", "debounce_seconds",
             "max_delete_percent", "transfers", "init_transfers", "checkers", "bwlimit",
             "pause_on_battery", "pause_on_metered", "pause_stops_running")


def emit(value):
    print(json.dumps(dict(value, schema_version=1), ensure_ascii=False))


def failure(args, code, message):
    if getattr(args, "json", False):
        emit(dict(ok=False, error=dict(code=code, message=message)))
    else:
        print(message, file=sys.stderr)
    return 1


def require_yes(args):
    # Never read stdin: a pipe, an agent or a GUI subprocess must not hang.
    if not args.yes:
        raise ValueError(i18n.YES_REQUIRED)


def reload_daemon():
    if dbus_service is not None:
        try:
            dbus_service.call("ReloadConfig")
        except BUS_ERRORS:
            pass  # Offline config edits remain usable before installation/login.


def folder_command(paths, args):
    action = "add" if args.command == "add-folder" else args.action
    if action == "excludes":
        return folder_excludes_command(paths, args)
    if action == "list":
        value = dict(folders=configuration.load(paths, create=False)["folders"])
    else:
        if action == "remove":
            require_yes(args)
        with configuration.edit_lock(paths):
            data = configuration.load(paths)
            if action == "add":
                local = Path(args.local).expanduser().absolute()
                name = local.name if args.name is None else args.name
                data = app_model.new_folder(data, str(local), name,
                    exists=local.is_dir(), symlink=local.is_symlink(), canonical=str(local.resolve()),
                    configured_roots=[str(Path(f["local"]).expanduser().resolve()) for f in data["folders"]])
                value = dict(folder=data["folders"][-1])
            else:
                folder = configuration.folder_by_id(data, args.id)
                if action == "remove":
                    data = app_model.remove_folder(data, args.id)
                    value = dict(id=args.id, removed=True)
                else:
                    folder["enabled"] = action == "enable"
                    value = dict(folder=folder)
            configuration.save(paths, data)
        reload_daemon()
    if args.json:
        emit(value)
    elif action == "add":
        print(i18n.FOLDER_ADDED.format(id=value["folder"]["id"]))
    elif action == "list":
        for folder in value["folders"]:
            print(json.dumps(folder, ensure_ascii=False))
    else:
        print(i18n.CLI_DONE)
    return 0


def folder_excludes_command(paths, args):
    def change(data):
        folder = configuration.folder_by_id(data, args.id)
        patterns = folder.setdefault("excludes", [])
        if args.exclude_action == "add":
            if args.pattern not in patterns:
                patterns.append(args.pattern)
        elif args.exclude_action == "remove":
            if args.pattern not in patterns:
                raise ValueError(i18n.CONFIG_ERROR.format(field="folders.excludes"))
            patterns.remove(args.pattern)
            if not patterns:
                del folder["excludes"]
        configuration.validate(data)
        return dict(id=args.id, patterns=list(folder.get("excludes", [])),
                    effective=configuration.effective_excludes(data, folder))
    if args.exclude_action == "list":
        value = change(configuration.load(paths, create=False))
    else:
        with configuration.edit_lock(paths):
            data = configuration.load(paths)
            value = change(data)
            configuration.save(paths, data)
        reload_daemon()
    if args.json:
        emit(value)
    else:
        print(json.dumps(value, ensure_ascii=False))
    return 0


def config_command(paths, args):
    read_only = args.action == "get" or args.action == "excludes" and args.exclude_action == "list"
    def change(data):
        if args.action in ("get", "set"):
            if args.action == "set":
                try:
                    value = args.value if args.key in ("root_folder", "language", "bwlimit") else json.loads(args.value)
                except ValueError:
                    raise ValueError(i18n.CONFIG_ERROR.format(field=args.key)) from None
                data[args.key] = value
                configuration.validate(data)
            return dict(key=args.key, value=data[args.key])
        if args.exclude_action == "add":
            if args.pattern not in data["excludes"]:
                data["excludes"].append(args.pattern)
        elif args.exclude_action == "remove":
            if args.pattern not in data["excludes"]:
                raise ValueError(i18n.CONFIG_ERROR.format(field="excludes"))
            data["excludes"].remove(args.pattern)
        configuration.validate(data)
        return dict(patterns=data["excludes"])
    if read_only:
        value = change(configuration.load(paths, create=False))
    else:
        with configuration.edit_lock(paths):
            data = configuration.load(paths)
            value = change(data)
            configuration.save(paths, data)
        reload_daemon()
    if args.json:
        emit(value)
    else:
        print(json.dumps(value, ensure_ascii=False))
    return 0


def submit_init(paths, args):
    """Persist the user's approval, then hand the job to the daemon.

    The daemon owns the job: it resumes it after a reboot or logout, so nothing
    here may depend on this process or on a transient systemd unit.
    """
    kind = args.command
    if not dbus_service.daemon_running():
        return failure(args, "daemon_unavailable", i18n.INIT_NO_DAEMON)
    with folder_lock(paths, args.id):
        data = configuration.load(paths, create=False)
        folder = configuration.folder_by_id(data, args.id)
        if not folder["enabled"]:
            raise ValueError(i18n.DISABLED)
        rclone.check_local(folder)
        store = StateStore(paths)
        item = store.read()["folders"].get(args.id, folder_state())
        digest = filters.digest_of(configuration.effective_excludes(data, folder))
        fingerprint = configuration.pair_hash(data, folder)
        plan = item.get("dry_run_plan")
        if not rclone.dry_run_valid(item, digest, fingerprint) or not isinstance(plan, dict):
            raise ValueError(i18n.DRY_RUN_REQUIRED)
        request = dict(kind=kind, approved_at=now(), approved_plan=init_flow.approved_plan(plan),
                       filters_sha256=digest, pair_sha256=fingerprint)
        store.folder(args.id, init_request=request, init_attempts=0, init_next_retry_at=None,
                     status="init_queued", last_error=None, needs_resync=item["needs_resync"])

    def revert():
        def mutate(value):
            current = value["folders"].get(args.id)
            if current and (current.get("init_request") or {}).get("approved_at") == request["approved_at"]:
                current.update(init_request=None, status=item["status"], last_error=item["last_error"])
        store.update(mutate)
    try:
        accepted = dbus_service.call("StartInit", args.id, kind)
    except BUS_ERRORS:
        revert()
        return failure(args, "daemon_unavailable", i18n.INIT_NO_DAEMON)
    if not accepted:
        revert()
        raise ValueError(i18n.INIT_SUBMIT_REFUSED)
    emit(dict(id=args.id, kind=kind, queued=True)) if args.json else print(i18n.INIT_SUBMITTED.format(id=args.id))
    return 0


def main(argv=None):
    args = parser().parse_args(argv)
    paths = Paths.from_env()
    try:
        if args.command == "config-path":
            print(paths.config)
            return 0
        if args.command == "setup-remote":
            if args.replace:
                require_yes(args)
            result = setup(paths, args.client_json, args.remote, args.replace)
            reload_daemon()
            return result
        if args.command == "doctor":
            from .doctor import report
            value = report(paths, offline=args.offline)
            if args.json:
                emit(value)
            else:
                for check in value["checks"]:
                    print(i18n.DOCTOR_LINE.format(**check))
                    print(json.dumps(check["details"], ensure_ascii=False))
            return 0 if value["ok"] else 1
        if args.command in ("folder", "add-folder"):
            return folder_command(paths, args)
        if args.command == "config":
            return config_command(paths, args)
        if args.command == "progress":
            data = configuration.load(paths, create=False)
            folder = configuration.folder_by_id(data, args.id)
            item = StateStore(paths).read()["folders"].get(args.id, folder_state())
            value = dict(item, id=args.id, enabled=folder["enabled"], initialized=folder["initialized"])
            if args.json:
                emit(value)
            else:
                # Same status and progress wording as the window's folder rows.
                label = i18n.STATUSES.get(app_model.folder_row(folder, value).status, i18n.STATUSES["idle"])
                progress = progress_text(item.get("progress"))
                if progress:
                    print(i18n.PROGRESS_LINE.format(id=args.id, status=label, progress=progress))
                else:
                    print(f"{args.id}: {label}")
            return 0
        if GLib is None:
            raise ValueError(i18n.GI_REQUIRED)
        if args.command in ("init", "resync"):
            foreground = args.foreground
            if (args.dry_run and (args.background or foreground) or args.background and foreground
                    or args.json and foreground):
                raise ValueError(i18n.REQUEST_REFUSED)
            if not args.dry_run:
                require_yes(args)
            if not args.dry_run and not foreground:
                return submit_init(paths, args)
            return direct(paths, args.id, dry_run=args.dry_run, resync=args.command == "resync",
                          json_output=args.json)
        if args.command == "force-sync":
            require_yes(args)
            return direct(paths, args.id, force=True)
        if args.command == "status":
            value = status(paths)
            emit(value) if args.json else print_status(value)
            return 0
        if args.command == "logs":
            value = status(paths)
            if args.id:
                if not any(folder["id"] == args.id for folder in value["folder_config"]):
                    raise ValueError(i18n.UNKNOWN_FOLDER.format(id=args.id))
                path = value["folders"].get(args.id, {}).get("last_log")
            else:
                logs = [f.get("last_log") for f in value["folders"].values() if f.get("last_log")]
                path = max(logs, key=lambda p: Path(p).name) if logs else None
            print(path or i18n.NO_LOG)
            return 0
        if args.command in ("sync", "resume"):
            prerequisites.check(configuration.load(paths))
        if args.command == "sync":
            accepted = dbus_service.call("SyncNow", args.id or "")
        else:
            accepted = dbus_service.call("Pause" if args.command == "pause" else "Resume")
        print(i18n.REQUEST_OK if accepted else i18n.REQUEST_REFUSED)
        return 0 if accepted else 1
    except BUS_ERRORS:
        return failure(args, "daemon_unavailable", i18n.NO_DAEMON)
    except (configuration.ConfigError, StateError, BusyError, ValueError) as error:
        code = "busy" if isinstance(error, BusyError) else "invalid_request"
        return failure(args, code, str(error))
    except (OSError, subprocess.SubprocessError):
        return failure(args, "operation_failed", i18n.SYNC_ERROR)
    except KeyboardInterrupt:
        print(i18n.INTERRUPTED, file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
