# Changelog

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
