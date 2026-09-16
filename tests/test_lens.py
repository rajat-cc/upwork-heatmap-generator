from __future__ import annotations

from core.lens import Lens, run_lens


def test_funnel_lens_conforms_and_runs_on_empty_db(isolated_db, monkeypatch, capsys):
    import config
    from features.funnel.api import FunnelLens

    monkeypatch.setattr(config, "EXPORTS_DIR", str(isolated_db.parent / "exports"))
    lens = FunnelLens()
    assert isinstance(lens, Lens)
    report = run_lens(lens, 30)
    assert report.events == 0
    out = capsys.readouterr().out
    assert "No proposal events" in out
    assert not (isolated_db.parent / "exports" / "funnel_latest.xlsx").exists()  # nothing to export
