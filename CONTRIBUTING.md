# Contributing

DriveSync uses Python 3.12, the standard library and system GI bindings only. Do not add
pip/runtime dependencies. Install the apt packages listed in [README.en.md](README.en.md),
plus `xvfb`, `dbus`, and `dbus-x11` for isolated GUI/session tests. Use a current rclone (>= 1.66).
Clone the repository and run launchers from `bin/`; no editable install is needed.

```bash
python3 -m unittest discover -s tests
# For CI/headless GUI coverage:
xvfb-run -a dbus-run-session -- python3 -m unittest discover -s tests
```

Tests isolate HOME, XDG directories and rclone configuration. Installer tests use temporary
homes. rclone download/checksum tests use fakes: never download an installer during the suite.
Real-rclone integration tests use **two local temporary directories only**, an empty temporary
rclone config and a restricted environment; they never use Google Drive or a real account.
They discover rclone on PATH, then the user-local binary, or accept an explicit binary:

```bash
DRIVESYNC_TEST_RCLONE=/path/to/rclone python3 -m unittest discover -s tests
```

Missing display/session facilities and missing real rclone are reported as skips. CI installs
them and runs with Xvfb and a private D-Bus session; investigate any skips there.
Never run `install.sh` or `uninstall.sh` against your real home merely to test a patch.
For manual installation experiments set HOME and all XDG roots to temporary directories and use
`--no-enable`. Never run cloud integration tests without a separate, explicit authorization.

Use four spaces, clear English identifiers/comments, small functions and stdlib `unittest`.
Keep safety decisions and presentation logic independent of GI when possible.
All Turkish UI strings live in `src/drivesync/i18n.py`. GTK 3 and GTK 4 must remain in separate
processes. Prefer argv arrays over shell commands, atomic private writes, and explicit errors
that cannot expose credentials. Preserve unknown config keys and existing user configuration.

Before opening a PR:

- Describe the user-visible behavior, safety implications and relevant validation.
- Run the full suite; report counts and skipped tests with reasons.
- Cover deletion guards, locks, preview validity and recovery when changing sync behavior.
- Update both README languages and the technical spec when commands/config change.
- Remove personal paths, emails, OAuth JSON, tokens, logs and screenshots with personal data.
- Confirm no Google/network access or real user-directory mutations occurred during tests.

Screenshots in `docs/screenshots/` are generated from demo data with
`python3 tools/make_screenshots.py` (needs a graphical session; it runs in an isolated
D-Bus session and temporary XDG directories and never touches real settings or Drive).

Report bugs with the issue template. Do not attach full config files or OAuth credentials.
Contributions are licensed under the repository's MIT license.

Maintainers preparing a public release must also follow [docs/RELEASE.md](docs/RELEASE.md),
particularly its clean-history requirement for the pre-release private development commits.
