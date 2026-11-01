---
name: Bug report / Hata bildirimi
about: Report a reproducible DriveSync problem
labels: bug
---

**Problem / Sorun**
What happened, and what did you expect?

**Steps to reproduce / Tekrarlama adımları**
1.
2.

**Environment / Ortam**
- Distribution and version:
- Desktop environment, version and X11/Wayland:
- DriveSync version (`drivesync-cli --version`):
- rclone version (`rclone version`):

**Status / Durum**
Paste `drivesync-cli status` output here, after removing personal information.

**Relevant logs / İlgili kayıtlar**
Paste a short excerpt from the log located by `drivesync-cli logs ID`, or
`journalctl --user -u drivesync`. Initial sync: `journalctl --user -u drivesync-init-ID`.

**Privacy reminder / Gizlilik**
Remove personal emails, folder/file names, paths, project/account IDs and machine names.
Never share client JSON, client secrets, access/refresh tokens, rclone.conf or backups.
