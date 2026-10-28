"""update_list: slug from paste, archiving older pastes of the same list, run diffs."""

import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import build_hub as H      # noqa: E402
import update_list as U    # noqa: E402

FIX = os.path.join(HERE, "fixtures")


def _row(ticker, score, gate=None, ccy="USD"):
    return {"ticker": ticker, "currency": ccy, "company": f"{ticker} Inc",
            "score_0_100": score, "gate": gate}


def _write_run(out_dir, slug, stamp, generated_at, rows):
    path = H.rows_json_path(out_dir, slug, stamp)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"schema": H.SCHEMA, "slug": slug, "title": "Test list",
                   "snapshot_date": generated_at[:10], "generated_at": generated_at,
                   "region": "us_eu", "take_model": "sonnet", "report": {},
                   "rows": rows}, fh)
    return path


def test_slug_matches_pipeline_rule():
    assert U.slug_for_paste(os.path.join(FIX, "dividend_table.txt")) == \
        "growing-dividend-payers-with-2-5-yield"


def test_archive_moves_only_same_list(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    old = inputs / "dividends_20261001.txt"
    new = inputs / "dividends_20261028.txt"
    other = inputs / "growth_20261001.txt"
    shutil.copy(os.path.join(FIX, "dividend_table.txt"), old)
    shutil.copy(os.path.join(FIX, "dividend_table.txt"), new)
    shutil.copy(os.path.join(FIX, "global_plaintext.txt"), other)

    moved = U.archive_older_pastes(str(new), U.slug_for_paste(str(new)), str(inputs))

    assert moved == [str(inputs / "archive" / "dividends_20261001.txt")]
    assert sorted(os.listdir(inputs)) == ["archive", "dividends_20261028.txt",
                                          "growth_20261001.txt"]


def test_diff_added_dropped_moves_gates():
    prev = {"rows": [_row("AAA", 50), _row("BBB", 40), _row("CCC", 70, gate=None),
                     _row("DDD", 30)]}
    new = {"rows": [_row("AAA", 65), _row("BBB", 45), _row("CCC", 70, gate="loss"),
                    _row("EEE", 80), _row("FFF", None)]}
    d = U.diff_runs(prev, new, move_threshold=10)
    assert [r["ticker"] for r in d["added"]] == ["EEE", "FFF"]   # by score, n/s last
    assert [r["ticker"] for r in d["dropped"]] == ["DDD"]
    assert [(r["ticker"], a, b) for r, a, b in d["moves"]] == [("AAA", 50, 65)]
    assert [(r["ticker"], a, b) for r, a, b in d["gates"]] == [("CCC", None, "loss")]


def test_same_ticker_other_currency_is_another_company():
    d = U.diff_runs({"rows": [_row("SAN", 50, ccy="EUR")]},
                    {"rows": [_row("SAN", 50, ccy="USD")]})
    assert len(d["added"]) == 1 and len(d["dropped"]) == 1


def test_report_uses_two_newest_runs(tmp_path):
    out = str(tmp_path)
    _write_run(out, "test-list", "20261001-0800", "2026-10-01T08:00:00", [_row("OLD", 10)])
    _write_run(out, "test-list", "20261015-0800", "2026-10-15T08:00:00", [_row("AAA", 50)])
    _write_run(out, "test-list", "20261028-0800", "2026-10-28T08:00:00",
               [_row("AAA", 62), _row("NEW", 77)])
    _write_run(out, "test-list-2", "20261028-0900", "2026-10-28T09:00:00", [_row("ZZZ", 1)])

    text = U.report(out, "test-list", 10)
    assert "2026-10-15T08:00:00 -> 2026-10-28T08:00:00" in text
    assert "1 new, 0 dropped, 1 moved 10+ pts" in text
    assert "NEW Inc (NEW) score 77" in text
    assert "AAA Inc (AAA) 50 -> 62 (+12)" in text
    assert "ZZZ" not in text and "OLD" not in text


def test_report_first_run(tmp_path):
    _write_run(str(tmp_path), "solo", "20261028-0800", "2026-10-28T08:00:00", [_row("A", 1)])
    assert "nothing to compare" in U.report(str(tmp_path), "solo", 10)
