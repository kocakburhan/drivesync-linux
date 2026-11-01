"""Pure initialization commands, preview parsing and deletion confirmation."""

from dataclasses import dataclass
import json
import re

from . import i18n


@dataclass(frozen=True)
class Plan:
    to_remote: int
    to_local: int
    deletes: int
    unknown: int
    actions: tuple

    @property
    def summary(self):
        return i18n.INIT_SUMMARY.format(to_remote=self.to_remote, to_local=self.to_local, deletes=self.deletes)

    def can_start(self, folder_name, typed=""):
        return self.unknown == 0 and (self.deletes == 0 or typed == folder_name)


def parse_plan(output):
    try:
        value = json.loads(output)
        counts = [value[key] for key in ("to_remote", "to_local", "deletes", "unknown")]
        actions = value["actions"]
        if (any(type(n) is not int or n < 0 for n in counts) or not isinstance(actions, list)
                or len(actions) > 50 or any(not isinstance(a, str) for a in actions)):
            raise ValueError
        return Plan(*counts, tuple(actions))
    except (ValueError, KeyError, TypeError):
        raise ValueError(i18n.INIT_PLAN_INVALID) from None


def init_label(folder, state):
    if (not folder.get("enabled", True) or state.get("status") in ("syncing", "queued")
            or (folder.get("initialized") and not state.get("needs_resync"))):
        return None
    return i18n.INIT_RESYNC if folder.get("initialized") else i18n.INIT_START


def preview_command(cli, folder_id):
    return [cli, "init", folder_id, "--dry-run", "--json"]


def launch_command(cli, folder_id, environment):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", folder_id):
        raise ValueError(i18n.INIT_PLAN_INVALID)
    command = ["systemd-run", "--user", "--unit=drivesync-init-" + folder_id, "--collect",
               "--property=Type=exec"]
    for name in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "RCLONE_CONFIG"):
        if environment.get(name):
            command.append("--setenv=" + name + "=" + environment[name])
    # Both GUI and CLI callers have already obtained explicit user approval.
    return command + ["--", cli, "init", folder_id, "--yes"]
