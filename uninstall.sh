#!/usr/bin/env bash
set -euo pipefail
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
operation=uninstall
source "$source_dir/data/install-common.sh"

if [[ $no_enable = false ]]; then
    # Already absent/inactive units are normal on repeated uninstallation.
    systemctl --user disable --now drivesync-tray.service drivesync.service || true
fi
for name in drivesync-daemon drivesync-tray drivesync-cli drivesync-app; do
    rm -f -- "$prefix/bin/$name"
done
rm -rf -- "$prefix/lib/drivesync"
rm -f -- "$unit_dir/drivesync.service" "$unit_dir/drivesync-tray.service"
rm -f -- "$desktop_dir/io.github.kocakburhan.DriveSync.desktop"
rm -f -- "$extension_dir/drivesync.py"
for name in ok sync error; do
    rm -f -- "$icon_dir/scalable/emblems/emblem-drivesync-$name.svg"
done
rm -f -- "$icon_dir/scalable/apps/io.github.kocakburhan.DriveSync.svg"
for name in ok syncing paused error offline idle; do
    rm -f -- "$icon_dir/symbolic/apps/drivesync-$name-symbolic.svg"
    rm -f -- "$icon_dir/scalable/status/drivesync-tray-$name.svg"
done
for frame in {0..7}; do
    rm -f -- "$icon_dir/symbolic/apps/drivesync-syncing-$frame-symbolic.svg"
    rm -f -- "$icon_dir/scalable/status/drivesync-tray-syncing-$frame.svg"
done
refresh_desktop_caches
if [[ $purge = true ]]; then
    PYTHONPATH="$source_dir/src" python3 - <<'PY'
import shutil
from drivesync.paths import Paths
paths = Paths.from_env()
for directory in (paths.config_dir, paths.state_dir, paths.data_dir):
    if directory.is_symlink():
        directory.unlink()
    elif directory.exists():
        shutil.rmtree(directory)
PY
fi
if [[ $no_enable = false ]]; then
    systemctl --user daemon-reload
fi
echo 'DriveSync kaldırıldı.'
if [[ $purge = false ]]; then
    echo 'Yapılandırma, durum ve veriler korundu.'
fi
