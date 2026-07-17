from __future__ import annotations

import prepare_stage5_challenge as challenge


def test_pinned_challenge_selection_is_fixed_and_unique():
    assert challenge.RECORD_ID == "16964503"
    assert challenge.VERSION == "v2.0.0"
    assert challenge.LICENSE == "CC BY 4.0"
    assert len(challenge.SAMPLE_SPECS) == 12
    ids = [item[0] for item in challenge.SAMPLE_SPECS]
    filenames = [item[1] for item in challenge.SAMPLE_SPECS]
    assert len(ids) == len(set(ids))
    assert len(filenames) == len(set(filenames))
    assert all(filename.endswith(".wav") for filename in filenames)


def test_plan_mode_does_not_download(monkeypatch, capsys):
    monkeypatch.setattr(challenge, "execute", lambda: (_ for _ in ()).throw(AssertionError("downloaded")))
    assert challenge.main([]) == 0
    output = capsys.readouterr().out
    assert "Plan only" in output
    assert "306.0 MiB" in output


def test_srt_timestamp_rounding():
    assert challenge._srt_timestamp(0) == "00:00:00,000"
    assert challenge._srt_timestamp(65.4324) == "00:01:05,432"
