# DriveSync — Technical Specification

This file is the authoritative technical spec for implementers. User setup is in README.md / README.en.md.

## Goal

A Linux (Zorin OS 18 / Ubuntu 24.04, GNOME 46, **X11**) desktop app that keeps selected local folders in two-way sync with
Google Drive under `gdrive:Linux-PC/<name>`, using `rclone bisync` as the sync engine. Our code orchestrates rclone and
provides a Turkish UI.

## Hard constraints

- Python 3.12, **standard library + system GI bindings only** (`gi`: GLib, Gio, Gtk 3 for tray, Gtk 4 + Adw 1 for windows,
  Notify 0.7, AyatanaAppIndicator3 0.1). **No pip packages.**
- rclone >= 1.66. New configs resolve `rclone` on PATH first, otherwise `~/.local/bin/rclone`, and store the absolute resolved path. Remote name `gdrive:` (configurable).
- Never print, log or copy tokens / client secrets / `rclone.conf` contents.
- All user-facing strings in **Turkish**. Code, identifiers, comments, commit messages in English.
- Installation into `~/.local` via `install.sh`; `uninstall.sh` removes it (never removes user data or config unless `--purge`).
- Data safety above everything: if unsure, stop syncing that folder and report an error rather than risk deletions.

## Paths

| What | Path |
|---|---|
| Config | `~/.config/drivesync/config.json` |
| State (JSON, written atomically) | `~/.local/state/drivesync/state.json` |
| Logs (one file per run, keep last 200 runs, plus `daemon.log` rotated at 5 MB) | `~/.local/state/drivesync/logs/` |
| bisync workdir | `~/.local/share/drivesync/bisync/` |
| Generated filter file | `~/.local/share/drivesync/filters.txt` |

Respect `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_DATA_HOME` when set (tests rely on this).

## Config schema (`config.json`)

```json
{
  "version": 1,
  "rclone_path": "/usr/bin/rclone",
  "remote": "gdrive:",
  "root_folder": "Linux-PC",
  "language": "auto",
  "poll_interval_minutes": 5,
  "full_sync_interval_minutes": 60,
  "debounce_seconds": 10,
  "max_monitors": 20000,
  "max_delete_percent": 25,
  "transfers": 4,
  "init_transfers": 8,
  "checkers": 8,
  "assumed_upload_bytes_per_second": 1048576,
  "init_extra_flags": ["--drive-pacer-min-sleep", "10ms", "--drive-pacer-burst", "200"],
  "paused": false,
  "excludes": ["node_modules/", ".venv/", "venv/", "__pycache__/", "build/", "dist/", ".git/",
               "*.tmp", "*.swp", "*~", ".~lock.*", "~$*"],
  "folders": []
}
```

- `language`: `"auto"` | `"tr"` | `"en"`. Precedence: config `tr`/`en` > env `DRIVESYNC_LANG` > locale (`LANGUAGE`, `LC_ALL`,
  `LC_MESSAGES`, `LANG`; Turkish if it starts with `tr`, otherwise English). Strings live in `src/drivesync/i18n.py`.
- Remote path of a folder = `<remote><root_folder>/<remote_name>` e.g. `gdrive:Linux-PC/Documents`.
- `initialized=false` means the folder has never completed a `--resync`; the daemon must NOT run normal bisync on it.
- Unknown keys are preserved on save. Missing keys get defaults. Config is validated; invalid config → clear Turkish error.
- `transfers`, `init_transfers` and `checkers` must be positive integers (booleans are invalid).
  `init_extra_flags` is a list of nonempty argument strings, passed as individual argv entries; an empty list disables
  the extra flags. No shell parsing is performed.
- New config has an empty `folders` list and `root_folder="Linux-PC"`. Users explicitly add folders.
  Existing config files are not rewritten on ordinary load/install; unknown fields and existing paths/folders are preserved.
  Explicit `install.sh --with-rclone` selects the freshly verified user-local binary in `rclone_path`.
- Folder example (explicitly added): `{"id":"documents", "local":"~/Documents", "remote_name":"Documents", "enabled":true, "initialized":false}`.
- Startup and direct sync check `rclone version` locally (>= 1.66) and `rclone listremotes` for the exact configured name.
  Missing/old binaries and missing remotes produce Turkish diagnostics with the README install link in CLI,
  daemon state, tray and window. Failed startup checks disable queueing and are retried every 30 seconds.

## Filters

Generate `filters.txt` from `excludes`: each entry becomes `- <pattern>`; a pattern ending in `/` excludes that directory at
any depth (rclone filter syntax: `- node_modules/**` style — implement correctly for rclone, e.g. `- **/node_modules/**`
and `- node_modules/**`). The `.drivesync-check` file must never be excluded. Regenerate whenever config changes.
Note: bisync stores a hash of the filters file and requires `--resync` when it changes. Detect a filter change (store
sha256 in state) and mark affected folders as needing resync (`needs_resync=true`, status error with Turkish message)
instead of silently running.

## rclone bisync invocation

Normal run (per folder):

```
rclone bisync <local> <remote_path>
  --workdir <workdir> --filters-file <filters.txt>
  --resilient --recover --max-lock 5m
  --conflict-resolve newer --conflict-loser delete
  --check-access --check-filename .drivesync-check
  --max-delete <max_delete_percent>
  --drive-skip-gdocs --skip-links --create-empty-src-dirs
  --compare size,modtime
  --transfers <transfers> --checkers <checkers>
  --log-file <run log> --log-level INFO --stats 30s --stats-one-line
```

- Real CLI init/resync bisync runs use `--transfers <init_transfers> --checkers <checkers>` and append
  `init_extra_flags` (by default `--drive-pacer-min-sleep 10ms --drive-pacer-burst 200`). Normal daemon runs,
  force-sync and dry runs use `transfers` and `checkers` without the init-only extra flags.
- Folder state `status=syncing` is authoritative for both window and tray even when daemon fields are idle.
  An uninitialized active folder displays “İlk eşitleme sürüyor” without an init hint. Mixed current and
  uninitialized enabled folders display counts, e.g. “✓ 1 klasör güncel · 5 klasör ilk eşitleme bekliyor”.
- Reconciliation, startup recovery and queueing leave externally locked folders alone. The CLI identifies its
  already-held folder lock when reconciling its own state; an unlocked interrupted run can still be recovered.

- `--check-access` requires a `.drivesync-check` file to exist in the root of BOTH sides. Initialization creates it
  (local file with short Turkish explanation text, then copied to remote with `rclone copyto`).
- Exit codes: 0 ok; 7 = fatal (bisync aborted, needs `--resync` or manual fix) → folder status `error` with
  `needs_resync=true`; others → `error` but retry next cycle. Parse the run log for lines containing `ERROR` and
  `New or changed in both paths` warnings to fill `last_error` (first 3 ERROR lines, truncated to 500 chars) and
  `conflicts` (number of distinct conflicting paths, not diagnostic lines).
- If the max-delete safety trips (log contains "too many deletes" / "Safety abort"), set status `blocked_delete` with
  Turkish message and **do not** retry automatically; user must explicitly confirm (`drivesync-cli force-sync <id> --yes`
  runs once with `--force`).

Initialization (per folder), explicitly requested through CLI or GUI, never automatic:

1. `drivesync-cli init <id> --dry-run [--json]` → first runs `rclone mkdir <remote_path>` (including the root folder),
   reports that the Drive folder is ready, then runs `rclone bisync ... --resync --resync-mode newer --dry-run` and prints a
   summary (counts of files to copy each way, to delete; first 50 lines of planned actions) only on success.
   Creating an empty remote directory is the only remote data mutation allowed during this preview.
   With `--json`, stdout is one JSON document: nonnegative integer `to_remote`, `to_local`, `deletes`,
   `unknown` and `actions` (first 50 action strings). Failures return nonzero and diagnostics on stderr;
   `--json` without `--dry-run` is rejected except when submitting to the daemon. Runtime JSON errors use the AGENTS.md contract.
   On failure, print a Turkish failure message and the first ERROR lines, exit non-zero, and leave
   `dry_run_ok_at=null`. An uninitialized folder stays `not_initialized`, with `needs_resync=false` and the
   diagnostic in `last_error`. A successful preview clears its `last_error` and `needs_resync`.
   State loading also clears stale `needs_resync` and error status for uninitialized folders, preserving diagnostics.
2. `drivesync-cli init <id> --yes` (requires prior successful dry-run within 24h for the same filter hash, recorded in state;
   otherwise refuse with message). Creates remote folder (`rclone mkdir`), writes `.drivesync-check` both sides, runs
   `--resync --resync-mode newer`, sets `initialized=true` in config on success.
   Remote mkdir is confined to CLI init/resync (including dry-runs); the daemon never creates remote roots.
   Initial upload can take hours: stream progress (the `--stats` lines) to stdout and to the run log.

## Daemon (`drivesync-daemon`)

- Single process, GLib main loop, runs as systemd user service.
- Exactly one instance (own the D-Bus name; exit if taken).
- Sync queue: folders are synced **one at a time** (global serialization), using `Gio.Subprocess` (non-blocking).
  Duplicate queue entries are merged.
- Timer: every `poll_interval_minutes`, enqueue all `enabled && initialized && !needs_resync && status != blocked_delete`
  folders unless `paused`.
- Remote change detection (`remote_watch.py`): on each poll a folder whose last full bisync is younger than
  `full_sync_interval_minutes` (default 60) is not synced blindly. One `rclone backend query <remote>
  "(modifiedTime > 'T' or createdTime > 'T') and trashed = false"` (whole Drive, JSON array with `id`, `name`,
  `mimeType`, `parents`, `modifiedTime`, `createdTime`, ...) lists items changed since the oldest folder check.
  Items are attributed to folders by `parents` using a cache of Drive directory IDs under
  `<remote><root_folder>` (`rclone lsjson --dirs-only -R --fast-list`, stored in
  `<data>/remote-index/<folder id>.json`; refreshed lazily after a full bisync and once when a changed item has an
  unknown parent; still unknown means outside our tree and ignored). Only attributed folders are enqueued
  (reason `remote-change`). Items without `parents` cannot be attributed: every checked folder whose last full sync is
  older than `poll_interval_minutes` is enqueued. Unchanged folders get `last_remote_check = check start - 2 minutes`
  (state.json). A successful bisync sets `last_remote_check` and `last_full_sync` to its finish time.
  Trashing/deleting on Drive may not change `modifiedTime`, so remote deletions are only guaranteed to be found by the
  periodic full bisync. Any failure of the check (network, quota, old rclone) logs once and falls back to the old
  behaviour: full bisync on the poll interval. A folder never fully synced since daemon state exists is always full-synced.
- Local changes use recursive Gio directory monitors and trailing-edge debounce, then
  `enqueue(folder_id, "local-change")`. Skip excluded directories/symlinks, add new directories,
  ignore a folder’s events during its run and for 5 seconds afterward. `max_monitors` is a positive
  integer (default 20000, global cap); overflow falls back to polling for that folder with one warning.
  Read-only measurement and limitations: `docs/WATCHERS.md`.
- Network check before each run: if `rclone lsf <remote><root_folder> --max-depth 1 --dirs-only` fails with a network
  error, mark daemon state `offline` and skip; restore the folder's prior status and keep the probe error only in
  `daemon.last_error`. Clear it on a successful probe; notify only if offline > 30 minutes continuously.
- Config file watched (`Gio.FileMonitor`); reload on change.
- Graceful shutdown on SIGTERM/SIGINT: let running rclone finish up to 60 s, then terminate it (bisync `--recover`
  handles interruption).

### State (`state.json`)

```json
{
  "daemon": {"status": "idle|syncing|paused|offline|error", "current_folder": null, "pid": 123, "started_at": "ISO", "last_error": null},
  "filters_sha256": "…",
  "recent_conflicts": [
    {"path": "nested/report.txt", "folder": "documents", "folder_name": "Documents",
     "folder_local": "/home/user/Documents", "time": "ISO with timezone", "winner": "local|remote|null"}
  ],
  "folders": {
    "documents": {
      "status": "ok|syncing|queued|error|blocked_delete|not_initialized|disabled",
      "last_sync_started": "ISO", "last_sync_finished": "ISO", "last_success": "ISO",
      "last_exit_code": 0, "last_error": null, "conflicts": 0, "needs_resync": false,
      "last_log": "/path/to/run.log", "dry_run_ok_at": "ISO", "dry_run_filters_sha256": "…"
    }
  }
}
```

Write atomically (temp file + `os.replace`). Timestamps ISO-8601 with timezone.
`recent_conflicts` retains the last 100 resolved conflicts from successful non-preview runs.
The rclone `The winner is: Path1/Path2` line identifies local/remote; otherwise winner is null.
History and folder results are written in one state transaction. Clearing history preserves all other state.

### D-Bus API (session bus)

Bus name `io.github.kocakburhan.DriveSync`, object `/io/github/kocakburhan/DriveSync`, interface
`io.github.kocakburhan.DriveSync1`:

| Member | Signature | Notes |
|---|---|---|
| `GetStatus()` | `→ s` | JSON string = state.json content plus `paused` and folder config summary |
| `SyncNow(s folder_id)` | `→ b` | empty string = all eligible folders |
| `StartInit(s folder_id, s kind)` | `→ b` | kind `init`/`resync`; runs the approved `folders.<id>.init_request` already persisted by the CLI. False if there is no matching request |
| `Pause()` / `Resume()` | `→ b` | persists `paused` in config |
| `ReloadConfig()` | `→ b` | |
| signal `StatusChanged(s json)` | | emitted on every state change (debounced to max 2/s) |

## Notifications

Via `Notify` (libnotify GI), app name "DriveSync", Turkish text. Only for: sync error (once per folder until it recovers),
`blocked_delete`, needs_resync, conflicts resolved (> 0, summary "N dosyada çakışma: en yeni sürüm tutuldu"),
offline > 30 min. Never notify on routine success. Initialize libnotify lazily when sending a notification,
so CLI commands that do not send notifications never initialize it. Libnotify portal selection depends on sandbox
recognition or `NOTIFY_FORCE_PORTAL`, not on the display app name or desktop-entry hint.

## Tray (`drivesync-tray`) — GTK 3 + AyatanaAppIndicator3, separate process

- Icon by overall state: ok / syncing / paused / error (use themed icon names with fallbacks, e.g.
  `emblem-synchronizing-symbolic`, `emblem-ok-symbolic`, `media-playback-pause-symbolic`, `dialog-warning-symbolic`;
  a custom SVG set will be added later — make icon names configurable in one place).
- If no enabled folder is initialized, show "İlk eşitleme bekliyor" with a neutral folder icon. Show "Güncel" only
  when at least one enabled initialized folder exists and all enabled initialized folders have status `ok`.
- Menu (Turkish): status line (insensitive, e.g. "Güncel · Son eşitleme: 3 dk önce" / "Eşitleniyor: Documents" /
  "Duraklatıldı" / "Hata: 1 klasör"), "Şimdi eşitle", "Duraklat"/"Devam et" (toggle), "Kayıtları aç" (opens logs dir),
  "Drive'da aç" (opens https://drive.google.com/drive/my-drive), separator, "Pencereyi aç"
  (runs `drivesync-app` if present on PATH or beside the tray launcher, otherwise disabled). No "Quit" item.
- Subscribes to `StatusChanged`; if daemon not running, shows "Arka plan servisi çalışmıyor" and a "Servisi başlat" item
  (`systemctl --user start drivesync.service`). Refresh relative times every 60 s.

## Main window (`drivesync-app`) — GTK 4 + libadwaita 1

- Single-instance `Adw.Application`, id `io.github.kocakburhan.DriveSync.App`; activation presents the window.
  Closing the window leaves the daemon running. GTK 3 and GTK 4 frontends remain separate processes,
  sharing the asynchronous D-Bus client and pure presentation helpers.
- Approved layout: overall status, sync/pause controls, folder action rows and menus, warning banner,
  add-folder chooser, settings/logs/Drive buttons. StatusChanged updates the view; a service status page
  offers `systemctl --user start drivesync.service` when the daemon is unavailable.
- Adding a validated local directory and unique safe remote component writes `enabled=true, initialized=false`.
  After adding, offer first sync using the same row flow. Removing a confirmed row only edits config.
- Uninitialized/needs-resync rows offer “İlk eşitlemeyi başlat” / “Yeniden eşitle”. A background
  `drivesync-cli init <id> --dry-run --json` subprocess shows a spinner, then an Adw.MessageDialog
  summarizes directional counts and deletions, with an expandable first-50 action list. Deletions require
  typing the exact Drive folder name; unknown actions block launch until investigated.
  “Başlat” asynchronously runs `drivesync-cli init|resync <id> --yes --json`, which persists the approval and
  submits the job to the daemon (`StartInit`). The daemon owns the job, so it survives window closure, logout
  and reboot. Rows show “İlk eşitleme sürüyor” (`syncing`), “İlk eşitleme sırada (yeniden başlatma sonrası devam
  edecek)” (`init_queued`) or “Yeniden onay gerekiyor” (`needs_approval`); the latter's button (“Yeniden onayla”)
  re-opens the dry-run review flow. Pure flow logic is in init_flow.py.
- Preferences edit poll interval, debounce, max-delete and excludes using existing config persistence and
  ReloadConfig. A Turkish warning explains that exclude changes require preview/resync of initialized folders.
- “Son çakışmalar (N)” opens a modal libadwaita list with path, folder, timestamp, winning side,
  “Klasörü aç” and “Temizle”. Clearing uses the shared state transaction lock and ReloadConfig to refresh clients.

## Nautilus extension

Nautilus 4.0 InfoProvider/MenuProvider, installed through a `drivesync.py` loader in the user's
XDG `nautilus-python/extensions` directory. Pure mapping/exclude/emblem logic lives in `nautilus_model.py`.
Custom hicolor emblems show approximate local status: ok, syncing/queued or modified since last_success,
error/blocked_delete/needs_resync. Uninitialized, disabled and excluded paths get no emblem.
Each file-info callback synchronously stats its local path without following symlinks and reads config/state
through SnapshotCache, which checks JSON metadata at most once per second and rereads only changed files.
No extension worker threads are used. A one-second timer reconnects the bus and detects snapshot changes;
it never scans remembered file mtimes. Snapshot changes and StatusChanged invalidate affected files through
a bounded weak registry. No rclone or synchronous D-Bus calls.
Menus call folder-specific SyncNow and open a URL-encoded Drive filename search. Installation prints a
restart note but never restarts Nautilus. GNOME 43+ display findings and limits: `docs/NAUTILUS.md`.

## CLI (`drivesync-cli`)

The complete command list, JSON schemas, doctor check ids, safety rules and agent Setup Playbook
are maintained in [AGENTS.md](../AGENTS.md#cli-reference-and-machine-contracts).

`doctor [--offline] [--json]` checks prerequisites, installation, services and folder state without
creating config/state. `folder list|add|remove|enable|disable` and `config get|set` /
`config excludes list|add|remove` avoid hand-editing JSON. Configuration changes notify ReloadConfig.
`status --json` and `progress <id> --json` expose state to agents.

All confirming CLI commands require explicit `--yes`; no command reads a confirmation from stdin,
including on a TTY. Real init/resync still require a recent successful dry-run. `init|resync <id> --yes`
(`--background` is an alias) records the approval and submits it to the daemon over D-Bus, then returns; the
daemon rechecks preview validity. Without a running daemon the command fails with a message suggesting
`systemctl --user start drivesync.service` or `--foreground`, which runs the job in the CLI process (not reboot-safe).
The GUI supplies --yes the same way.

#### Daemon-owned init/resync (reboot-safe)

On a real request the CLI stores, after the mandatory successful dry-run (`folders.<id>.dry_run_plan`):
`folders.<id>.init_request = {"kind": "init"|"resync", "approved_at": ISO, "approved_plan": {"to_remote", "to_local",
"deletes"}, "filters_sha256", "pair_sha256"}` and status `init_queued`. The daemon runs it as a job in its serialized
queue (same engine/folder locks, `init_transfers`, live progress): `mkdir`, optional fresh dry-run, marker `copyto`,
`bisync --resync`. On success the folder is marked initialized and `init_request` is cleared.
On daemon start (and after interrupted runs, which are put back to `init_queued`) every pending request first runs a
FRESH dry-run (skipped only when the approval's own dry-run is still valid and no run has started). It resumes
automatically only if the fresh plan has 0 deletes, 0 unknown actions and `to_local` <= the approved `to_local`;
changed paths/filters also stop it. Otherwise nothing runs: status `needs_approval`, the request is kept and the
user is notified (“İlk eşitleme yeniden onay bekliyor: <ad>”). Failures retry with backoff 1, 5, 15, 60 minutes
(`init_attempts`, `init_next_retry_at`), then give up (request cleared, error shown); network loss waits without
consuming a retry.
Commands use the same execution/folder locks as the daemon.
The `status` table shows each `last_error` as one line: first ERROR message, timestamp/level removed, at most 80 characters
(including an ellipsis when truncated). Full diagnostics remain in state and logs.

### OAuth setup helper

`setup-remote` accepts only an `installed` OAuth Desktop JSON. It validates the name, refuses an
existing remote without `--replace` (the CLI also requires `--yes`), and holds the execution lock. On replacement it locates the
actual rclone.conf via `rclone config file`, writes a private backup to the DriveSync config directory
before changing anything, and copies the client JSON to `google-client.json` (0600, XDG-aware).
It invokes `rclone config create <name> drive client_id=… client_secret=… scope=drive` for browser consent,
clears the old token on replacement to force new consent, then verifies with `rclone about <name>:`. Credentials/backend output never enter UI/logs.
Successful setup also selects the remote name in DriveSync configuration, using an atomic config edit.

## Install

- `install.sh`: copies package to `~/.local/lib/drivesync/`, wrappers to `~/.local/bin/` (`drivesync-daemon`,
  `drivesync-tray`, `drivesync-cli`, `drivesync-app`), installs the desktop entry and hicolor SVG icons,
  systemd user units `drivesync.service` (daemon, `Restart=on-failure`) and
  `drivesync-tray.service` (PartOf graphical-session.target, `After=drivesync.service`), autostart tray via the
  graphical-session target, creates default config if missing, runs `systemctl --user daemon-reload` and enables both.
  Idempotent. Checks required GI typelibs and rclone >= 1.66, prints Turkish messages and install help.
  `--with-rclone` uses the stdlib install_rclone module: download version.txt, then the matching
  linux-amd64/arm64 ZIP and SHA256SUMS from downloads.rclone.org, verify the exact filename/hash,
  atomically install only its binary into ~/.local/bin (0755), never sudo. Failure preserves the old binary.
  Installer tests inject fake download payloads and never use the network.
- `uninstall.sh`: stops/disables units, removes installed files. `--purge` also removes config/state/data.

## Repository layout

```
src/drivesync/__init__.py, paths.py, config.py, filters.py, rclone.py, state.py, queue.py,
              daemon.py, watcher.py, dbus_service.py, client.py, notify.py, cli.py, tray.py,
              app.py, app_model.py, presentation.py, i18n.py (Turkish strings in one place)
bin/ (thin launchers)   data/systemd/*.service   install.sh  uninstall.sh
tests/ (unittest; fake rclone script in tests/fixtures/fake_rclone.py that records argv and simulates exit codes/logs)
```

Tests run with `python3 -m unittest discover -s tests` and must not touch the real home, network or Drive.
Unit tests use isolated XDG paths and fake rclone. `tests/test_integration_rclone.py` uses real rclone from
`DRIVESYNC_TEST_RCLONE` (default PATH then `~/.local/bin/rclone`, module skipped if missing) with two local temporary trees,
an empty temporary `RCLONE_CONFIG`, and temporary HOME, XDG and cache paths.
