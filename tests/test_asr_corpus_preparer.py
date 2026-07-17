from __future__ import annotations

import prepare_asr_benchmark_corpus as preparer


def test_corpus_preparer_is_pinned_and_plan_only_by_default(capsys, monkeypatch) -> None:
    assert len(preparer.REVISION) == 40
    assert preparer.LICENSE == "CC BY 4.0"
    assert set(preparer.SHARDS) == {"fr_fr", "en_us", "cmn_hans_cn"}
    monkeypatch.setattr("sys.argv", ["prepare_asr_benchmark_corpus.py"])
    assert preparer.main() == 0
    output = capsys.readouterr().out
    assert "Plan only" in output
    assert "1.44 GiB" in output
