from __future__ import annotations

import plistlib

from core import service


def test_plist_is_a_periodic_job_not_a_daemon(tmp_path):
    plist = service.build_plist(
        python="/usr/bin/python3", working_dir="/repo", interval=7200, logs=tmp_path
    )
    assert plist["Label"] == "com.upwork-intel.sync"
    assert plist["ProgramArguments"] == ["/usr/bin/python3", "/repo/main.py", "sync"]
    assert plist["WorkingDirectory"] == "/repo"
    assert plist["StartInterval"] == 7200
    assert plist["RunAtLoad"] is True
    assert "KeepAlive" not in plist  # launchd would otherwise restart the finished job
    assert plist["StandardOutPath"].startswith(str(tmp_path))


def test_install_dry_run_returns_valid_plist_without_touching_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "plist_path", lambda: tmp_path / "x.plist")
    path, data = service.install(interval=600, dry_run=True)
    assert path == tmp_path / "x.plist"
    assert not path.exists()
    parsed = plistlib.loads(data)
    assert parsed["StartInterval"] == 600
