#!/usr/bin/env python3
"""Read-only watch startup measurement; no config, state, rclone or main loop.

Usage: /usr/bin/python3 tools/measure_watchers.py ~/Projects [--max-monitors 20000]
Uses built-in excludes. Directory names are scanned; file contents are not read.
All Gio monitors are cancelled before exit. This is deliberately outside tests.
"""

import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gi.repository import GLib
from drivesync.config import defaults
from drivesync.watcher import FolderWatchers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--max-monitors", type=int, default=20000)
    args = parser.parse_args()
    root = args.path.absolute()
    if not root.is_dir() or root.is_symlink() or args.max_monitors < 1:
        parser.error("Var olan, sembolik bağlantı olmayan klasör ve pozitif sınır gerekli.")
    data = defaults()
    data.update(max_monitors=args.max_monitors, folders=[dict(id="measurement", local=str(root),
                enabled=True, initialized=True)])
    watchers = FolderWatchers(lambda *_: None, GLib.timeout_add, GLib.source_remove)
    try:
        started = time.perf_counter()
        watchers.configure(data)
        print(f"İzleyici: {watchers.count}; kurulum: {time.perf_counter() - started:.3f} saniye; "
              f"en yüksek izleyici: {watchers.peak_count}; neden: {list(watchers.fallback_reasons.values())}; "
              f"aralıklı kontrole dönüş: {'evet' if watchers.fallback else 'hayır'}")
    finally:
        watchers.close()


if __name__ == "__main__":
    main()
