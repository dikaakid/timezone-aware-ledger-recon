import shutil
from pathlib import Path

import pandas as pd
import pytest

from ledger_recon.cli import main
from ledger_recon.config import ReconConfig
from ledger_recon.pipeline import run_batch

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "dummy_data"


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    shutil.copytree(SAMPLE, d)
    return d


def test_batch_on_sample_data_matches_documented_numbers(data, tmp_path):
    out = tmp_path / "out"
    result = run_batch(data, out, ReconConfig(), log=lambda *_: None)
    day1 = result["days"][pd.Timestamp("2026-01-05").date()]
    assert day1 == {
        "total_rows": 71,
        "recon": 62,
        "unrecon": 9,
        "bank_only": 5,
        "ledger_only": 4,
        "ambiguous_rows": 4,
        "wrong_routing": 1,
        "weak_matches": 2,
        "amount_mismatch_rows": 2,
        "rejected_rows": 3,
    }
    assert not (out / "recon_2026-01-07.xlsx").exists()  # incomplete statement skipped
    # Two recheck resolutions: the cross-midnight pair on Jan 5/6 (Pass 1),
    # and the Pass 0 pair where the ledger arrived Jan 5 but the bank did
    # not post until Jan 8.
    assert result["recheck"]["resolved"] == 4


def test_second_run_skips_finished_dates_and_force_overrides_guard(data, tmp_path):
    out = tmp_path / "out"
    run_batch(data, out, ReconConfig(), log=lambda *_: None)
    again = run_batch(data, out, ReconConfig(), log=lambda *_: None)
    assert again["days"] == {}
    forced = run_batch(data, out, ReconConfig(), force=["2026-01-07"], log=lambda *_: None)
    assert (out / "recon_2026-01-07.xlsx").exists() and len(forced["days"]) == 1


def test_report_contains_all_sheets(data, tmp_path):
    run_batch(data, tmp_path / "out", ReconConfig(), log=lambda *_: None)
    names = pd.ExcelFile(tmp_path / "out" / "recon_2026-01-05.xlsx").sheet_names
    assert names == [
        "Statement Raw",
        "Ledger Raw",
        "Line Per Line",
        "Non-Reference Rows",
        "Rejected Rows",
        "Summary",
        "Pivot per Account",
    ]


def test_cli_pair_mode(data, tmp_path, capsys):
    code = main(
        [
            "pair",
            str(data / "statement_2026-01-05.csv"),
            str(data / "ledger_2026-01-05_2026-01-06.csv"),
            "--date",
            "2026-01-05",
            "-o",
            str(tmp_path / "r.xlsx"),
        ]
    )
    assert code == 0 and (tmp_path / "r.xlsx").exists() and "recon" in capsys.readouterr().out


def test_config_json_rejects_unknown_keys(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"nope": 1}')
    with pytest.raises(ValueError, match="Unknown config keys"):
        ReconConfig.from_json(p)


def test_readme_pair_command_example(tmp_path):
    """The exact pair command from README must succeed.

    This guards against a broken README example. If we ever move or rename
    a sample file, this test fails and forces us to update both the test and
    the README, keeping them in sync.

    See README.md section "One day only".
    """
    from pathlib import Path

    from ledger_recon.cli import main

    root = Path(__file__).parent.parent
    out = tmp_path / "report.xlsx"

    rc = main(
        [
            "pair",
            str(root / "examples/dummy_data/statement_2026-01-05.csv"),
            str(root / "examples/dummy_data/ledger_2026-01-05_2026-01-06.csv"),
            "--date",
            "2026-01-05",
            "--metabase",
            str(root / "examples/dummy_data/metabase_2026-01-05.csv"),
            "-o",
            str(out),
        ]
    )

    assert rc == 0
    assert out.exists()
    assert out.stat().st_size > 1000, "report.xlsx should not be empty"


def test_classify_unrecon_counts_covers_every_row():
    """The four categories must sum to the reported unrecon count."""
    from ledger_recon.pipeline import _classify_unrecon_counts

    lpl = pd.DataFrame(
        [
            {"Metabase_Status": "Unrecon - pending GDS", "Amount_Mismatch": False},
            {"Metabase_Status": "Unrecon - confirmed missing", "Amount_Mismatch": False},
            {"Metabase_Status": "Unrecon - pending bank posting", "Amount_Mismatch": False},
            {"Metabase_Status": "Unrecon - ledger only, no confirmation", "Amount_Mismatch": False},
            {"Metabase_Status": None, "Amount_Mismatch": True},
            {"Metabase_Status": None, "Amount_Mismatch": True},
        ]
    )
    summary = {"unrecon": 6}
    out = _classify_unrecon_counts(lpl, summary)
    assert out == {"bank": 2, "ledger": 2, "noref": 0, "mismatch": 2}


def test_classify_unrecon_counts_raises_on_imbalance():
    """A row that falls into no category triggers a RuntimeError."""
    from ledger_recon.pipeline import _classify_unrecon_counts

    lpl = pd.DataFrame(
        [
            {"Metabase_Status": "Unrecon - pending GDS", "Amount_Mismatch": False},
            {"Metabase_Status": None, "Amount_Mismatch": False},  # uncounted
        ]
    )
    summary = {"unrecon": 2}
    with pytest.raises(RuntimeError, match="does not add up"):
        _classify_unrecon_counts(lpl, summary)


def test_metabase_guard_fires_only_for_a_truncated_export(data, tmp_path):
    """The export-window guard must skip Day 9 (empty export) but not Day 6.

    Day 6 has exactly one ledger row in the WIB early window -- the
    cross-midnight twin whose Metabase counterpart is on Day 1. That
    is incidental, not evidence of a truncated export, so the guard
    must stay silent. Day 9 has an intentionally empty Metabase file
    and a ledger row at 04:00 WIB, so the guard must fire and skip
    verification.

    The test reads the log rather than the report so it does not depend
    on how the report writer lays columns out.
    """
    logs: list[str] = []
    run_batch(data, tmp_path / "out", ReconConfig(), log=logs.append)
    text = "\n".join(logs)

    assert "Metabase export looks incomplete for 2026-01-09" in text
    assert "Metabase export looks incomplete for 2026-01-06" not in text
