"""
config/persistence.py
─────────────────────
Phase O — optional persistent storage for the SMALL files that must survive a
Railway redeploy (admin content + push-device list).

How the data directory is chosen (first match wins):
  1. PERSISTENT_DATA_DIR   — explicit override (Railway → Variables)
  2. RAILWAY_VOLUME_MOUNT_PATH — set automatically by Railway when a Volume is
                                 attached to the service
  3. neither set           — behaviour is exactly as before: files live at
                             their old relative paths on the normal disk

If the chosen directory cannot be created or written to, we log a warning and
fall back to the old relative paths, so a bad volume never takes the backend
down.

Only the files passed to `resolve()` are moved. Scan results, logs, caches,
tokens and uploads are deliberately NOT routed through here.

One-time safe first deploy: the first time a file is resolved on an empty
volume, an existing legacy file on the normal disk is COPIED in (never moved,
never overwriting a file already on the volume).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile


def _configured_dir() -> str:
    for name in ("PERSISTENT_DATA_DIR", "RAILWAY_VOLUME_MOUNT_PATH"):
        value = str(os.getenv(name, "") or "").strip()
        if value:
            return value
    return ""


def _usable(path: str) -> bool:
    """True if `path` exists (or can be created) and a file can be written there."""
    try:
        os.makedirs(path, exist_ok=True)
        fd, probe = tempfile.mkstemp(prefix=".write_probe_", dir=path)
        os.close(fd)
        os.remove(probe)
        return True
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"[persistence] WARNING: data dir {path!r} is not writable ({exc}); "
              f"using local disk instead.")
        return False


_DATA_DIR: str = ""
_configured = _configured_dir()
if _configured and _usable(_configured):
    _DATA_DIR = _configured
    print(f"[persistence] Using persistent data dir: {_DATA_DIR}")


def data_dir() -> str:
    """The active persistent directory, or '' when persistence is off."""
    return _DATA_DIR


def resolve(filename: str) -> str:
    """
    Return the path a small persistent file should use.

    Persistence off  → `filename` unchanged (same relative path as today).
    Persistence on   → <data dir>/<filename>, copying the legacy file in once
                       if the volume does not have it yet.
    """
    if not _DATA_DIR:
        return filename

    target = os.path.join(_DATA_DIR, os.path.basename(filename))
    try:
        if not os.path.exists(target) and os.path.isfile(filename):
            shutil.copy2(filename, target)
            print(f"[persistence] Copied existing {filename} -> {target}")
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"[persistence] WARNING: could not copy {filename} to volume ({exc}).")
    return target


def atomic_write_json(path: str, obj, indent: int = 2) -> None:
    """
    Write JSON via a temp file + os.replace so a crash or restart mid-write
    can never leave a truncated file (which the loaders would read as empty).
    The temp file sits beside the target so the replace stays on one filesystem.
    """
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=indent)
    os.replace(tmp, path)
