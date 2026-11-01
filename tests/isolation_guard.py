"""Refuse to run a standalone smoke script against the real user's DriveSync directories.

The unittest suite always sets temporary XDG_* directories before launching these scripts.
Running a script directly without them would read and overwrite the live config/state.
"""
import os
from pathlib import Path
import sys
import tempfile


def require_isolated_xdg():
    home = Path.home().resolve()
    real = {"XDG_CONFIG_HOME": home / ".config", "XDG_STATE_HOME": home / ".local/state",
            "XDG_DATA_HOME": home / ".local/share"}
    temp_root = Path(tempfile.gettempdir()).resolve()
    for name, default in real.items():
        value = os.environ.get(name)
        path = Path(value).resolve() if value else default
        if path == default or temp_root not in path.parents:
            sys.exit(f"Refusing to run: {name} must point to a temporary directory "
                     f"(got {value or 'unset'}). Run it through `python3 -m unittest discover -s tests`.")
