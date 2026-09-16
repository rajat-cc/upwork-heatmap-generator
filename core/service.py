"""macOS LaunchAgent for the periodic `sync`.

Unlike the proposal agent's long-running service (RunAtLoad + KeepAlive), a
periodic job must NOT use KeepAlive: launchd would restart the finished
process every ThrottleInterval and burn API quota. `StartInterval` runs it
every N seconds; `RunAtLoad` runs it once at login so the data is fresh
after a reboot. `WorkingDirectory` matters because `.env`, the database and
the token cache are all resolved relative to the repo.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from config import ROOT_DIR

LABEL = "com.upwork-intel.sync"
DEFAULT_INTERVAL = 7200  # seconds


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_dir() -> Path:
    return Path.home() / "Library" / "Logs" / "upwork-intel"


def build_plist(
    *,
    python: str | None = None,
    working_dir: str | None = None,
    interval: int = DEFAULT_INTERVAL,
    logs: Path | None = None,
    extra_args: tuple[str, ...] = (),
) -> dict:
    python = python or sys.executable
    working_dir = working_dir or ROOT_DIR
    logs = logs or log_dir()
    return {
        "Label": LABEL,
        "ProgramArguments": [python, os.path.join(working_dir, "main.py"), "sync", *extra_args],
        "WorkingDirectory": working_dir,
        "StartInterval": int(interval),
        "RunAtLoad": True,
        "StandardOutPath": str(logs / "sync.log"),
        "StandardErrorPath": str(logs / "sync.err.log"),
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin")},
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False)


def install(*, interval: int = DEFAULT_INTERVAL, dry_run: bool = False) -> tuple[Path, bytes]:
    """Write the plist and (re)load it. Returns (path, plist bytes)."""
    plist = build_plist(interval=interval)
    data = plistlib.dumps(plist)
    path = plist_path()
    if dry_run:
        return path, data
    log_dir().mkdir(parents=True, exist_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", domain, str(path))  # ignore: not loaded yet on first install
    res = _launchctl("bootstrap", domain, str(path))
    if res.returncode != 0:
        # Older launchctl syntax as a fallback.
        res = _launchctl("load", "-w", str(path))
        if res.returncode != 0:
            raise RuntimeError(f"launchctl could not load {path}: {res.stderr.strip()}")
    return path, data


def uninstall() -> bool:
    """Unload and delete the plist. Returns True if something was removed."""
    path = plist_path()
    if not path.exists():
        return False
    _launchctl("bootout", f"gui/{os.getuid()}", str(path))
    _launchctl("unload", str(path))
    path.unlink(missing_ok=True)
    return True
