# DriveSync agent instructions

DriveSync synchronizes selected Linux folders with Google Drive in both directions using rclone bisync.
It provides a GTK/libadwaita window, tray, systemd user daemon and Nautilus integration.
It uses Python's standard library and GI; installation and configuration belong to the desktop user.

**If a user asks you to set up Google Drive on Linux with this repo, follow the Setup Playbook.**
Talk to the user in **the user's language**, even though this document is English.
This is the single source of truth for Codex, OpenCode, Claude Code, Gemini CLI, Copilot and other agents.
`CLAUDE.local.md` and `NOTLAR.md` are the owner's private, gitignored files: never modify them or copy
anything from them into tracked files. Preserve unrelated work in the checkout.

## Hard safety rules

- Never run a real **init, resync or force-sync** without first showing the dry-run result to the user
  and getting their explicit OK for that folder and plan. `--yes` records that approval; it does not replace it.
  If the plan shows **any deletions, prominently warn about the count and affected paths** before asking.
  Stop on unexpected actions, unknown actions or failed previews; investigate before trying again.
- Never print, paste or include OAuth client secrets, tokens or `rclone.conf` in responses, logs or commits.
  Pass the client JSON **path** to setup; do not read its credentials into a conversation.
- Never delete user files or Drive data as a setup/troubleshooting action. Normal approved sync can propagate
  deletions: explain that before approval. A preview may create a missing **empty** Drive target folder.
- Never edit `~/.config/rclone/rclone.conf` by hand. Use `setup-remote`; rclone owns credentials.
  Do not run `rclone sync` or `rclone bisync` directly: use `drivesync-cli`.
- Never disable the max-delete guard. Do not raise its value to evade a blocked deletion.
  `force-sync` is a one-run bypass, only for deliberately approved deletions after a fresh preview.
- Run as the normal graphical desktop user, never run DriveSync/install.sh with sudo.
  Only apt needs privilege. Ask the user to run that command or obtain their approval before running it.
- Do not assume sudo approval, browser access, Google consent or first-sync approval. Explain each user action.
  No CLI command reads a confirmation from stdin; confirming commands require `--yes`, including in a TTY.
  OAuth still requires browser interaction. Do not invent a successful installation or consent result.

## Setup Playbook

Use the commands from the repository directory. Replace example paths/names with the user's answers.
Commands below perform real setup **when the user requests setup**; during development use isolated tests only.

1. **Detect the environment.** Before installation the checkout launcher works:
   ```bash
   ./bin/drivesync-cli doctor --offline --json
   ```
   Inspect `environment.os`, `environment.desktop`, `python.version`, all `gi.*` checks and
   `desktop.appindicator`. Linux, Python >= 3.12 and a graphical systemd user session are expected.
   GNOME X11 is the primary tested desktop; Wayland can sync but check the tray and Nautilus visually.
   Missing install/remote checks are expected at this point. If Linux/Python is unsupported, stop and
   help the user prepare the environment; missing GI does not prevent doctor from reporting.

2. **Install system packages**, with the user's sudo approval or ask them to run this one command:
   ```bash
   sudo apt update && sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-notify-0.7 gir1.2-ayatanaappindicator3-0.1 python3-nautilus libnotify-bin git curl unzip
   ```
   Re-run `./bin/drivesync-cli doctor --offline --json`; every `gi.*` check should be `ok`.
   On apt failure, show the package manager error and resolve it before installation. On other distributions,
   find the equivalent system packages with the user. For GNOME, have the user enable **AppIndicator and
   KStatusNotifierItem Support** in Extensions; `desktop.appindicator` remains an advisory warning on GNOME
   because doctor cannot prove the tray is visible.

3. **Install rclone >= 1.66 without sudo** if `rclone.version` fails:
   ```bash
   ./install.sh --with-rclone
   export PATH="$HOME/.local/bin:$PATH"
   ```
   This installs DriveSync too; it downloads and checksum-verifies rclone for amd64/arm64.
   Verify `./bin/drivesync-cli doctor --offline --json` → `rclone.version`.
   On download/checksum failure, stop and retry only after resolving connectivity; the old binary is preserved.
   `--with-rclone` also selects that newly installed binary in existing configuration; other settings are preserved.
   Plain `./install.sh` preserves the existing binary selection. If doctor still fails, inspect the reported binary
   and installer error before proceeding.

4. **Install/update the app**:
   ```bash
   ./install.sh
   export PATH="$HOME/.local/bin:$PATH"
   drivesync-cli --version
   drivesync-cli doctor --offline --json
   ```
   Verify `install.files`, `install.units`, `systemd.drivesync.enabled`, `systemd.drivesync.active`,
   `systemd.drivesync-tray.enabled`, `systemd.drivesync-tray.active`, `config.valid`.
   If PATH is missing, use `~/.local/bin/drivesync-cli` now and arrange the user's session PATH.
   If units fail, inspect `systemctl --user status drivesync.service drivesync-tray.service` and
   `journalctl --user -u drivesync.service`; see the troubleshooting table. A missing Drive connection is expected.

5. **Create the user's Google OAuth client. This REQUIRES their browser and Google account.**
   Walk them through [docs/GOOGLE-CLIENT-ID.en.md](docs/GOOGLE-CLIENT-ID.en.md) **one step at a time**,
   in their language, waiting for each step. If you have browser/computer-use tools and the user agrees,
   operate with them watching; they perform sign-in and account/consent decisions. Otherwise give click instructions.
   Enable Drive API, create an External consent app and a **Desktop app** client, download its JSON.
   Explain **Audience → Publish app → In production**: Testing refresh tokens for full Drive access expire
   after seven days. Publishing is separate from Google verification; an unverified personal app may still warn.
   Explain homepage/privacy URL choices from the guide: use the project's public pages if accessible and suitable,
   or host adapted [privacy-template.md](docs/privacy-template.md) and a homepage on the user's own public site.
   If Google requires domain ownership, the user must use a domain they control. Local Markdown paths are not URLs.
   Never invent a domain or publish private contact information in this repository. If blocked, resolve the exact
   Console step with the user; do not proceed with a Web client or assume consent succeeded.

6. **Authorize the connection** (substitute the actual downloaded file path):
   ```bash
   drivesync-cli setup-remote --client-json "/actual/path/client.json"
   drivesync-cli doctor --json
   ```
   The browser opens for consent: **the user must select the account and click approval**.
   Verify `remote.configured` and `remote.reachable`. On failure, follow the OAuth guide and table; never expose
   provider output containing credentials. Reauthorization of an existing remote requires explicit approval:
   ```bash
   drivesync-cli setup-remote --client-json "/actual/path/client.json" --replace --yes
   ```
   Replacement backs up rclone configuration before changing it. `--remote NAME` supports a custom connection;
   successful setup also selects that connection in DriveSync without hand-editing JSON.

7. **Ask which local folders to sync and which Drive parent folder name to use** (default `Linux-PC`).
   Explain the resulting `My Drive / parent / folder` paths and two-way deletion behavior. Then use:
   ```bash
   drivesync-cli config set root_folder "Linux-PC" --json
   drivesync-cli folder add "/actual/local/Documents" --remote-name "Documents" --json
   drivesync-cli folder list --json
   ```
   Repeat `folder add` for each answer. Use the returned `folder.id`, never guess it from the name.
   Verify `config.valid` and `state.folders` in `drivesync-cli doctor --offline --json`.
   On validation failure, choose an existing, non-symlink, non-overlapping local directory and a unique safe Drive
   folder name. Adding/enabling a folder does not initialize it. Use CLI config/folder commands, not JSON edits.

8. **Preview and obtain approval for each folder separately**:
   ```bash
   drivesync-cli init FOLDER_ID --dry-run --json
   ```
   SHOW `to_remote`, `to_local`, `deletes`, `unknown` and `actions` to the user. Explain that at most 50 actions
   are returned; use `drivesync-cli logs FOLDER_ID` to locate the full log for private inspection when needed.
   SHOW the `size` object too: the local upload size and file count (`bytes`, `files`), the approximate duration
   (`estimated_seconds`, computed from `assumed_bytes_per_second`) and `largest_subfolders` (up to 5, with their
   `bytes` and `files`). If `size.large` is `true`, say that the folder is large and **offer to exclude** the largest
   subfolders the user does not need, e.g. `drivesync-cli config excludes add 'NAME/' --json`. Adding an exclude
   invalidates the preview: run the preview again and show it again before asking for approval.
   **If deletions > 0, warn prominently.** Get explicit OK before continuing. Do not proceed on `unknown > 0`
   or nonzero exit status. Then launch the approved plan:
   ```bash
   drivesync-cli init FOLDER_ID --background --yes --json
   ```
   This stores the user's approval (the approved plan, filters and path hashes) in state and hands the job to the
   **daemon**, then returns immediately with `queued: true`; multi-hour uploads outlive tool calls. The daemon must
   be running (otherwise: `systemctl --user start drivesync.service`, or run in the foreground with `--foreground`).
   The job **survives reboot and logout**: on every daemon start it re-runs a fresh dry-run and resumes
   automatically if the plan carries no new risk (0 deletes, no more downloads than approved); otherwise the
   folder becomes `needs_approval` and the user gets a notification. In that case repeat step 8 (preview, show,
   approve). Retries after failures are bounded (1, 5, 15, 60 minutes, then it gives up); offline simply waits.
   A real init requires a successful preview within 24 hours for the same paths, filters and deletion guard.
   On refusal, make a fresh preview and ask again. Follow progress with `drivesync-cli progress FOLDER_ID --json`
   and `drivesync-cli logs FOLDER_ID`. Do not start duplicate jobs.

9. **Verify completion**, polling with separate short tool calls:
   ```bash
   drivesync-cli progress FOLDER_ID --json
   drivesync-cli status --json
   drivesync-cli doctor --json
   ```
   `progress.progress` contains live stats while syncing and is null otherwise. Null alone does not mean success:
   check `status`, `last_exit_code`, `last_success`, `initialized`, `needs_resync` and errors. Verify `state.folders`
   and remote/unit checks. If a job fails, inspect logs, resolve the cause and preview/approve again.
   Tell the user where to find the app: application menu **DriveSync**, the tray icon and Nautilus right-click
   **DriveSync: Şimdi eşitle / Drive'da göster**. For missing Nautilus integration, ask before `nautilus -q`
   because it closes Files windows. Explain that closing the DriveSync window leaves uploads running.

## CLI reference and machine contracts

All commands are non-interactive by default, also when stdin is not a TTY. No confirmation prompts are read.
Real `init`/`resync`, `force-sync`, `folder remove` and `setup-remote --replace` require `--yes` in every session.
Other commands are reversible config edits or read-only; explicit `--yes` is not needed.
`folder remove` only removes config, never local files, Drive data, logs or saved state.
Changes through folder/config/setup commands notify a running daemon with D-Bus `ReloadConfig`.
An absent daemon does not prevent config edits.

```text
drivesync-cli doctor [--offline] [--json]
drivesync-cli folder list [--json]
drivesync-cli folder add LOCAL_PATH [--remote-name NAME] [--json]
drivesync-cli folder remove ID --yes [--json]
drivesync-cli folder enable ID [--json]
drivesync-cli folder disable ID [--json]
drivesync-cli folder excludes ID list [--json]
drivesync-cli folder excludes ID add PATTERN [--json]
drivesync-cli folder excludes ID remove PATTERN [--json]
drivesync-cli config get KEY [--json]
drivesync-cli config set KEY VALUE [--json]
drivesync-cli config excludes list [--json]
drivesync-cli config excludes add PATTERN [--json]
drivesync-cli config excludes remove PATTERN [--json]
drivesync-cli status [--json]
drivesync-cli progress ID [--json]
drivesync-cli init ID --dry-run [--json]
drivesync-cli init ID --yes [--json]            # submit to the daemon (same as --background)
drivesync-cli init ID --background --yes [--json]
drivesync-cli init ID --yes --foreground         # run in this process (lost on reboot)
drivesync-cli resync ID --dry-run [--json]
drivesync-cli resync ID --yes [--json|--foreground]
drivesync-cli force-sync ID --yes
drivesync-cli setup-remote --client-json PATH [--remote NAME] [--replace --yes]
drivesync-cli add-folder LOCAL_PATH [--name NAME] [--json]
drivesync-cli sync [ID]
drivesync-cli pause
drivesync-cli resume
drivesync-cli logs [ID]
drivesync-cli config-path
drivesync-cli update [--check] [--json]
drivesync-cli update --yes [--force-restart] [--json]
drivesync-cli --version
```

`add-folder` is the compatibility alias for `folder add`. Before `force-sync`, review
`resync ID --dry-run --json` and get explicit approval; the bypass works only for initialized folders
blocked by the deletion guard without a pending resync. Ordinary `sync` queues already initialized folders.
Quote paths/patterns. For a pattern starting with `-`, put options before the separator:
`drivesync-cli config excludes add --json -- '-example'`.

Safe scalar keys: `language` (`"auto"` default, `"tr"` or `"en"`; plain string, not JSON; sets the UI language of the app,
daemon, tray, CLI and Nautilus; `auto` follows `DRIVESYNC_LANG`, then `LANGUAGE`/`LC_ALL`/`LC_MESSAGES`/`LANG`),
`root_folder` (one safe path component, no `/`, `\`, `:`, control characters, `.` or `..`),
`poll_interval_minutes` (finite number > 0), `full_sync_interval_minutes` (finite number > 0, default 60:
safety-net full bisync interval; between full syncs a cheap Drive change check decides), `debounce_seconds` (finite number >= 0),
`max_delete_percent` (finite number 0–100), `transfers`, `init_transfers`, `checkers` (integers >= 1),
`bwlimit` (plain string, `""` = unlimited, passed as rclone `--bwlimit` to every bisync run; accepted forms: `1M`, `500k`,
`off`, upload:download split `10M:off`, timetable `"08:00,1M 23:00,off"` or `"Mon-08:00,512k:2M 22:00,off"`; sizes are
numbers with optional `b|k|M|G|T|P` (+`i`/`B`); no resync needed; a running transfer keeps its old limit, the next run uses the new one),
`pause_on_metered` (boolean, default `true`), `pause_on_battery` (boolean, default `false`) and `pause_stops_running`
(boolean, default `false`; also stops a running transfer gracefully when auto-pause starts): while a metered network
(Gio NetworkMonitor) or battery (UPower) condition holds, no new sync starts, work waits in the queue and the daemon status is
`auto_paused` (`status` JSON has `auto_pause: "metered"|"battery"|null`, separate from the manual `paused`).
`assumed_upload_bytes_per_second` (finite number > 0, default `1048576`; only converts the dry-run `size` into an estimated duration).
`check_updates` (`true` or `false`, default `true`: the daemon asks GitHub for the latest release at most once a day,
without credentials, and only announces it; `false` turns the check off).
`parallel_lanes` (boolean, default `true`): the daemon has two execution lanes, a **long** lane for init/resync and a
**normal** lane for routine bisync, each running at most one job, so routine syncs of other initialized folders keep
flowing during a multi-hour initial upload. Never two jobs on one folder; a folder with a pending/running init/resync is
never in the normal lane. `false` restores strictly one job at a time. With both lanes busy they share the link; a
configured `bwlimit` is applied to each job separately (so the total can reach twice the limit). Direct CLI runs take
the long lane (`init`/`resync`, also `--dry-run`) or the normal lane (`force-sync`) plus the folder lock.
Numbers use JSON number syntax; booleans/null are rejected for numeric keys (the `pause_*` keys, `check_updates` and `parallel_lanes` take `true`/`false`). Excludes must be nonempty strings without surrounding
whitespace or control characters. Adding the same pattern twice is idempotent; removing an absent pattern fails.
Per-folder excludes (subcommands `drivesync-cli folder excludes list`, `drivesync-cli folder excludes add` and
`drivesync-cli folder excludes remove`, each taking the folder ID first as in the command list above; stored as optional `"excludes"` in the folder entry) use the same
pattern rules and ADD to the global list; the folder's filter file is global + folder patterns. Changing a folder's
excludes needs a resync of that folder only; changing the global list affects every initialized folder.
Changing root/filters/guard invalidates previews; initialized folders can need a fresh approved resync.

JSON stdout is one object, never mixed with progress prose. Every successful JSON response has
`schema_version: 1`; additional fields may be added compatibly. Exit 0 means the command succeeded;
exit 1 means failure, 2 means argument syntax error, 130 means interrupted. Doctor exits 0 iff no check is `fail`
(warnings are allowed). Status exits 0 when read successfully even if the daemon/folders report an error.
Runtime failures with `--json` return `{"schema_version":1,"ok":false,"error":{"code":"invalid_request","message":"…"}}`;
other codes are `busy`, `sync_failed`, `operation_failed`, `daemon_unavailable`. Human messages are Turkish or English (see `language`).
Argument syntax errors use Turkish stderr with exit 2; help/version are plain text. Interrupted runs use stderr.

- **doctor**: `{"schema_version":1,"ok":true,"checks":[{"id":"rclone.version","status":"ok","message":"…","fix":"./install.sh --with-rclone","details":{}}]}`.
  Every check has a stable `id`, `status` in `ok|warn|fail`, Turkish `message`, a concrete command or repository
  doc path in `fix`, and an object `details`. `--offline` skips only `rclone about`, marked `warn` with
  `details.skipped: true`; local `version`/`listremotes` still run. Reachability has an 8-second process timeout.
  No credentials or raw backend output are included. Doctor never creates config/state; typelib probes run in separate processes.
  `state.folders.details.folders` has each configured folder's id, enabled, initialized, status, needs_resync,
  last_success and progress. Inotify details include max_user_watches, estimated_need, estimate_complete and
  max_monitors; estimation ignores symlinks/excludes and caps at 100,000 directories (incomplete → warning).
- **folder list**: `{"schema_version":1,"folders":[{"id":"docs","local":"/path","remote_name":"Documents","enabled":true,"initialized":false}]}`.
  Add/enable/disable return `{"schema_version":1,"folder":{…}}`; remove returns `{"schema_version":1,"id":"docs","removed":true}`.
- **config get/set**: `{"schema_version":1,"key":"transfers","value":4}`;
  **config excludes list/add/remove**: `{"schema_version":1,"patterns":["*.tmp"]}`;
  **folder excludes ID list/add/remove**: `{"schema_version":1,"id":"docs","patterns":["*.psd"],"effective":["…global…","*.psd"]}`
  (`patterns` = the folder's own, `effective` = global + own).
- **init/resync dry-run**: `{"schema_version":1,"to_remote":1,"to_local":0,"deletes":0,"unknown":0,"actions":[],"size":{…}}`.
  Counts are nonnegative integers; actions are at most 50 strings. Failure never authorizes a real run.
  `size` (new fields may appear later) is the local estimate of what an upload would send: `bytes` and `files` are
  sums over regular files that the folder's global excludes do not match (symlinks are skipped, like `--skip-links`);
  `estimated_seconds` is `bytes / assumed_bytes_per_second`; `assumed_bytes_per_second` is the speed used;
  `large` is `true` when files > 20000 or bytes > 10 GiB; `largest_subfolders` lists up to 5 immediate subdirectories
  sorted by bytes, each `{"name","bytes","files"}`. The estimate is not a Drive measurement and can differ from rclone.
- **init/resync --yes (--background)**: `{"schema_version":1,"id":"docs","kind":"init","queued":true}`;
  this means the daemon accepted the approved request, not that synchronization finished. Without a running
  daemon the error code is `daemon_unavailable`.
- **status**: `{"schema_version":1,"daemon":{…},"paused":false,"folder_config":[…],"folders":{"docs":{…}},"recent_conflicts":[],…}`.
  The daemon object includes status, last_error, `current_folder` (long-lane folder if any, else the normal-lane one;
  kept for compatibility) and `current_folders` (list of every folder a lane runs now); folder objects include status, needs_resync, progress,
  last_success, last_exit_code, last_error and last_log. Without the daemon, saved state/config are returned
  with daemon.status `error` and a Turkish explanation.
- **update --check --json**: `{"schema_version":1,"current":"0.2.0","latest":"0.3.0","update_available":true,"url":"https://github.com/…"}`.
  Network failure: `{"schema_version":1,"ok":false,"error":{"code":"operation_failed","message":"…"}}`.
- **update --yes --json**: `{"schema_version":1,"updated":true,"previous":"0.2.0","version":"0.3.0","forced_restart":false}`.
  `update` never runs without `--yes`. It refuses (exit 1, `invalid_request`) when the recorded source checkout has
  uncommitted changes (`git status --porcelain`), is detached, or its branch does not track `origin`, and while a sync
  or an approved initial sync is running (unless `--force-restart`; the initial sync then resumes after the restart).
  It then runs `git pull --ff-only`, `./install.sh` and `systemctl --user restart drivesync.service drivesync-tray.service`
  in the source directory recorded by `install.sh` in `lib/drivesync/install-info.json` (`source_dir`, `version`, `installed_at`).
  A failed step stops the update (`operation_failed`); the notification action runs the same command in a transient user unit.
- **progress**: `{"schema_version":1,"id":"docs","enabled":true,"initialized":false,"status":"syncing","needs_resync":false,"progress":{…},…}`.
  Includes the saved folder-state fields described above. Live progress is an object from state (or null), with
  available transfer stats such as bytes_done, bytes_total, percent, speed, eta_seconds, files_done, files_total and updated_at; fields can be unknown.
  Folder/config/progress reads use defaults without creating a missing config or state file.

## Troubleshooting by doctor id

| Check id | Fix / verification |
| --- | --- |
| `environment.os` | Use Linux; see README.en.md requirements. |
| `environment.desktop` | Run in the normal graphical login; inspect XDG_CURRENT_DESKTOP and XDG_SESSION_TYPE. |
| `python.version` | Install/use the distro's Python >= 3.12, with its matching python3-gi. No pip. |
| `gi.GLib` | Run the apt command in step 2; re-run doctor. |
| `gi.Gio` | Run the apt command in step 2; re-run doctor. |
| `gi.GObject` | Run the apt command in step 2; re-run doctor. |
| `gi.Gtk3` | Install gir1.2-gtk-3.0 with step 2. |
| `gi.Gtk4` | Install gir1.2-gtk-4.0 with step 2. |
| `gi.Gdk4` | Install gir1.2-gtk-4.0 with step 2. |
| `gi.Adw` | Install gir1.2-adw-1 with step 2. |
| `gi.Notify` | Install gir1.2-notify-0.7 with step 2. |
| `gi.AyatanaAppIndicator3` | Install gir1.2-ayatanaappindicator3-0.1 with step 2. |
| `gi.Nautilus` | Install python3-nautilus with step 2; restart Files only with the user's approval. |
| `desktop.appindicator` | Enable AppIndicator and KStatusNotifierItem Support in GNOME Extensions; check the actual tray icon. |
| `rclone.version` | `./install.sh --with-rclone` installs and selects the verified local binary; inspect the reported path/version if it still fails. |
| `remote.configured` | Run setup-remote with the downloaded Desktop client JSON (step 6). |
| `remote.reachable` | Complete browser consent; check connectivity; for invalid_grant publish the app and reauthorize with --replace --yes after approval. See docs/GOOGLE-CLIENT-ID.en.md. |
| `install.files` | `./install.sh`; doctor lists missing file paths. |
| `install.units` | `./install.sh`; check custom DRIVESYNC_SYSTEMD_DIR if used. |
| `systemd.drivesync.enabled` | `systemctl --user enable --now drivesync.service`. |
| `systemd.drivesync.active` | `systemctl --user status drivesync.service`; inspect journalctl, fix prerequisites, then start the service. |
| `systemd.drivesync-tray.enabled` | `systemctl --user enable --now drivesync-tray.service`. |
| `systemd.drivesync-tray.active` | `systemctl --user status drivesync-tray.service`; inspect journalctl and GI checks. |
| `config.valid` | Run install.sh if absent. For invalid existing config, inspect docs/SPEC.md privately; use CLI safe scalar/exclude edits when loadable. Never overwrite it blindly. |
| `inotify.watches` | Compare estimated_need, max_user_watches and max_monitors; follow docs/WATCHERS.md. Polling remains available. System-wide limit changes require sudo approval. |
| `state.folders` | `drivesync-cli progress ID --json` and `drivesync-cli logs ID`; preview/approve init or resync as required. For corrupt saved state follow docs/SPEC.md; never erase baselines to bypass recovery. |

## Updating and uninstalling

Wait for active transfers (including detached init units) to finish. Preferred, from any shell:

```bash
drivesync-cli update --check
drivesync-cli update --yes      # refuses on local changes or a running sync; see the update JSON contract
drivesync-cli doctor --json
```

Manual equivalent, from the checkout recorded by `install.sh`:

```bash
git pull && ./install.sh
systemctl --user restart drivesync.service drivesync-tray.service
drivesync-cli doctor --json
```

Uninstall with `./uninstall.sh` (preserves config/state/bisync data and synced files).
`./uninstall.sh --purge` also removes DriveSync configuration/state/bisync data; only use when the user
explicitly requests that removal. Neither command deletes synced local files or Drive data; rclone and its
credentials remain installed. After purge, first sync needs a new preview and approval.

## Development

- Layout: `src/drivesync/cli.py` handles CLI dispatch; `doctor.py` diagnostics; `config.py`/`state.py` atomic
  persistence and locks; `prerequisites.py` local rclone checks; `setup_remote.py` OAuth; `init_flow.py` shared
  preview/submit commands; `rclone.py` builds/runs synchronization; `daemon.py` schedules it; `remote_watch.py` cheap Drive change detection (`rclone backend query`);
  `app.py`/`app_model.py`, `tray.py`, `nautilus_ext.py` provide desktop integration.
  `bin/` launchers, `data/` systemd/desktop/icons, `install.sh`/`uninstall.sh`, `tests/` and `docs/` complete the repo.
- Run the full suite: `python3 -m unittest discover -s tests`.
  Optional GUI/session verification: `xvfb-run -a dbus-run-session -- python3 -m unittest discover -s tests`.
- Integration tests use **local temporary directories only**, with empty temporary rclone configuration.
  Test all commands with fakes; never start real systemd units during tests. Never access the network or modify
  real `~/.config/drivesync`, `~/.local/state/drivesync`, `~/.local/share`, `~/.local/bin`,
  `~/.config/systemd`, or `~/.config/rclone` during development/testing.
- Keep dependencies **stdlib + gi only; no pip**. Put Turkish UI/help/error strings in `i18n.py`.
  Preserve atomic writes, unknown configuration fields, execution locks and preview validity checks.
- Update this document when adding a CLI subcommand or doctor check id; tests enforce coverage.
  When unsure about current library/framework APIs, consult Context7 MCP if available; skip it for familiar APIs.
