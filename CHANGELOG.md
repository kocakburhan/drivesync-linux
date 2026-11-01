# Changelog

## 0.4.0 — 2026-11-01

- Two execution lanes: an initial sync or resync no longer blocks routine syncs of other folders. Controlled by `parallel_lanes` (default `true`); `daemon.current_folders` shows what is running in each lane.
- Configurable conflict policy `conflict_policy`: `newer` (default, the most recently changed copy wins) or `keep_both`, where the older copy is kept as `<name>.drivesync-conflict1`.
- Opt-in encryption of the rclone configuration with a random password stored in the GNOME keyring: `drivesync-cli secure-config enable|disable|status`. Doctor checks `keyring.available` and `rclone.config_encrypted`. While the keyring is locked, the daemon waits instead of failing.
- Tests never reach the network: `DRIVESYNC_NO_NETWORK` disables the live update fetch.

## 0.3.0 — 2026-11-01

- Per-folder exclude rules: each folder gets its own filter file, so changing exclusions only requires a resync of that folder. Manage them with `drivesync-cli folder excludes` or the "Hariç tutmalar…" item in the folder row menu. Upgrades keep rclone's filter hash, so existing folders do not force a resync.
- Bandwidth limit `bwlimit`, including timetables for different hours of the day.
- Automatic pause on metered networks (on by default) and on battery power (off by default), controlled by `pause_stops_running`.
- Large-folder warning in the initial-sync review: total size, file count, estimated time and the largest subfolders. Each subfolder has an Exclude button that adds an anchored per-folder exclude rule. The time estimate uses `assumed_upload_bytes_per_second`.
- Notification buttons for Review and Show conflicts.
- Readable `drivesync-cli progress` output.
- Daily update check against GitHub releases, with a notification and a window banner. Use `drivesync-cli update [--check] [--yes]` to check or install an update.
- Smoke scripts refuse to run outside isolated temporary XDG directories.

## 0.2.0 — 2026-11-01

- Initial sync and resync now run in the background daemon and survive reboot or logout. The approval is stored; after a restart a fresh preview runs and the sync resumes only if no new deletions or downloads appear, otherwise approval is requested again. Retries are bounded.
- Cheap Drive change detection with `rclone backend query`: full bisync runs only on remote changes, plus a safety-net full sync every `full_sync_interval_minutes` (default 60).
- English user interface with automatic language selection and the `language` setting (auto/tr/en).
- README screenshots generated from demo data (`tools/make_screenshots.py`).
- New D-Bus method `StartInit`.

## 0.1.0 — 2026-11-01

- Selected-folder two-way Google Drive sync with rclone bisync and newer-wins conflicts.
- Turkish GTK/libadwaita window, system tray, notifications and live transfer progress.
- Local inotify monitoring, remote polling, pause/resume and Nautilus integration.
- Empty first-run configuration; resolved rclone discovery and version/remote diagnostics.
- Desktop OAuth JSON setup with private storage, replacement backups and access verification.
- GUI first-sync/resync previews, typed deletion confirmation and detached systemd jobs.
- CLI previews in JSON, folder addition, status, sync, logs and version reporting.
- Mandatory preview validity, execution locks, access markers and maximum deletion guards.
- SHA256-verified user-local rclone installer for Linux amd64/arm64.
- Turkish/English installation and Google OAuth guides; offline tests and CI.
