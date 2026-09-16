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
KINDS = ("sync", "digest")


def label_for(kind: str = "sync") -> str:
    return f"com.upwork-intel.{kind}"


def plist_path(kind: str = "sync") -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label_for(kind)}.plist"


def log_dir() -> Path:
    return Path.home() / "Library" / "Logs" / "upwork-intel"


def build_plist(
    *,
    python: str | None = None,
    working_dir: str | None = None,
    interval: int = DEFAULT_INTERVAL,
    logs: Path | None = None,
    extra_args: tuple[str, ...] = (),
    kind: str = "sync",
) -> dict:
    if kind not in KINDS:
        raise ValueError(f"unknown service kind {kind!r}")
    python = python or sys.executable
    working_dir = working_dir or ROOT_DIR
    logs = logs or log_dir()
    args = ["sync"] if kind == "sync" else ["digest", "--send"]
    plist: dict = {
        "Label": label_for(kind),
        "ProgramArguments": [python, os.path.join(working_dir, "main.py"), *args, *extra_args],
        "WorkingDirectory": working_dir,
        "RunAtLoad": kind == "sync",
        "StandardOutPath": str(logs / f"{kind}.log"),
        "StandardErrorPath": str(logs / f"{kind}.err.log"),
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin")},
    }
    if kind == "sync":
        plist["StartInterval"] = int(interval)
    else:  # weekly, Monday 08:00 local time
        plist["StartCalendarInterval"] = {"Weekday": 1, "Hour": 8, "Minute": 0}
    return plist


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False)


def install(
    *, interval: int = DEFAULT_INTERVAL, dry_run: bool = False, kind: str = "sync"
) -> tuple[Path, bytes]:
    """Write the plist and (re)load it. Returns (path, plist bytes)."""
    plist = build_plist(interval=interval, kind=kind)
    data = plistlib.dumps(plist)
    path = plist_path(kind)
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


def uninstall(kind: str = "sync") -> bool:
    """Unload and delete the plist. Returns True if something was removed."""
    path = plist_path(kind)
    if not path.exists():
        return False
    _launchctl("bootout", f"gui/{os.getuid()}", str(path))
    _launchctl("unload", str(path))
    path.unlink(missing_ok=True)
    return True
