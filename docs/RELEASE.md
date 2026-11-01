# Public release checks

The working tree is prepared for 0.4.0. Before publishing:

- **Publish clean history.** Existing private development commits retain personal defaults
  removed from the current source/docs/tests. Do not push that history to the public repository.
  Prepare a clean public snapshot/history while preserving the private development repository.
  The release preparation intentionally does not create commits or rewrite history.
- Run the full CI suite on Ubuntu 24.04 with Xvfb and a private D-Bus session. Restricted
  sandboxes can skip GTK window/tray and D-Bus smoke tests; those skips are not desktop validation.
- Manually check the OAuth guide, consent, fresh remote setup, first sync/resync and progress
  with a separate demo Google account. Automated tests never contact Google or download rclone.
- Confirm the public home/privacy URLs in both Google guides are reachable and appropriate;
  use the documented self-hosted option if unavailable or domain verification is required.
- Capture the screenshots listed in [screenshots/TODO.md](screenshots/TODO.md).
- Check the release version, changelog date and MIT license, then publish from the clean history.

Never publish private notes, OAuth JSON, rclone configuration/backups, logs or personal demo data.
