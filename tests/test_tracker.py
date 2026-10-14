import pandas as pd
import pytest
from conftest import REF

from ledger_recon.config import ReconConfig
from ledger_recon.tracker import (
    SHEET_CONSUMED,
    SHEET_RESOLVED,
    ensure_tracker_valid,
    recheck_tracker,
    record_consumed_ids,
    update_tracker,
)


def row(bank_id=None, ledger_id=None, ref=REF, amount_minor=10000000, bank_at=None, ledger_at=None):
    return {
        "reference": ref,
        "amount_minor": amount_minor,
        "bank_txn_id": bank_id,
        "ledger_id": ledger_id,
        "bank_posted_at": bank_at,
        "ledger_created_at": ledger_at,
        "Match?": "Unrecon",
    }


def pool(*items):
    """items: (ledger_id, created_at string)"""
    df = pd.DataFrame(
        [
            {"ledger_id": i, "reference": REF, "amount_minor": 10000000, "created_at": pd.Timestamp(t)}
            for i, t in items
        ]
    )
    df["ledger_date"] = df["created_at"].dt.date
    return df


def sheets(path):
    return pd.read_excel(path, sheet_name=None, dtype=str)


def test_months_use_year_and_month_so_years_never_collide(tmp_path):
    p = tmp_path / "t.xlsx"
    update_tracker(
        pd.DataFrame([row("B1", bank_at="2026-01-05 10:00:00"), row("B2", bank_at="2027-01-05 10:00:00")]), p
    )
    assert set(sheets(p)) == {"2026-01", "2027-01"}


def test_update_is_idempotent(tmp_path):
    p = tmp_path / "t.xlsx"
    rows = pd.DataFrame([row("B1", bank_at="2026-01-05 10:00:00")])
    update_tracker(rows, p)
    update_tracker(rows, p)
    assert len(sheets(p)["2026-01"]) == 1


def test_previous_tracker_is_backed_up_on_rewrite(tmp_path):
    p = tmp_path / "t.xlsx"
    update_tracker(pd.DataFrame([row("B1", bank_at="2026-01-05 10:00:00")]), p)
    update_tracker(pd.DataFrame([row("B2", bank_at="2026-01-05 11:00:00")]), p)
    assert len(list((tmp_path / "tracker_backups").glob("t_*.xlsx"))) == 1


def test_corrupt_tracker_stops_the_run(tmp_path):
    p = tmp_path / "t.xlsx"
    p.write_bytes(b"not a zip")
    with pytest.raises(SystemExit):
        ensure_tracker_valid(p)


def test_recheck_resolves_within_window_and_closes_the_ledger_only_twin(tmp_path):
    p = tmp_path / "t.xlsx"
    update_tracker(
        pd.DataFrame(
            [row("B1", bank_at="2026-01-05 23:59:30"), row(ledger_id="L9", ledger_at="2026-01-06 00:00:20")]
        ),
        p,
    )
    out = recheck_tracker(p, pool(("L9", "2026-01-06 00:00:20")), ReconConfig())
    s = sheets(p)
    assert out == {"resolved": 2, "still_pending": 0}
    assert len(s[SHEET_RESOLVED]) == 2 and s["2026-01"].empty
    assert "L9" in s[SHEET_CONSUMED]["ledger_id"].tolist()


def test_recheck_never_resolves_against_a_row_from_weeks_later(tmp_path):
    p = tmp_path / "t.xlsx"
    update_tracker(pd.DataFrame([row("B1", bank_at="2026-01-05 10:00:00")]), p)
    out = recheck_tracker(p, pool(("L9", "2026-01-27 10:00:00")), ReconConfig())  # 22 days later
    assert out["resolved"] == 0 and len(sheets(p)["2026-01"]) == 1


def test_a_consumed_ledger_id_cannot_resolve_a_second_row(tmp_path):
    p = tmp_path / "t.xlsx"
    update_tracker(
        pd.DataFrame([row("B1", bank_at="2026-01-05 10:00:00"), row("B2", bank_at="2026-01-05 11:00:00")]), p
    )
    out = recheck_tracker(p, pool(("L9", "2026-01-05 12:00:00")), ReconConfig())
    assert out["resolved"] == 1 and out["still_pending"] == 1
    record_consumed_ids(["L9"], p)
    assert recheck_tracker(p, pool(("L9", "2026-01-05 12:00:00")), ReconConfig())["resolved"] == 0


# --- _find_symmetric_pairs unit tests ---------------------------------------


def _bank_row(ref, amount, when, bank_id="B1"):
    return {
        "reference": ref,
        "amount_minor": amount,
        "bank_txn_id": bank_id,
        "ledger_id": None,
        "bank_posted_at": when,
        "ledger_created_at": None,
        "Amount_Mismatch": None,
    }


def _ledger_row(ref, amount, when, ledger_id="L1"):
    return {
        "reference": ref,
        "amount_minor": amount,
        "bank_txn_id": None,
        "ledger_id": ledger_id,
        "bank_posted_at": None,
        "ledger_created_at": when,
        "Amount_Mismatch": None,
    }


def test_find_symmetric_pairs_negative_gap():
    """Ledger arrived first, bank arrived later -> still a pair."""
    from ledger_recon.tracker import _find_symmetric_pairs

    sheets = {
        "2026-01": pd.DataFrame(
            [
                _ledger_row("REF1", "1000000", "2026-01-05 10:00:00", "L1"),
                _bank_row("REF1", "1000000", "2026-01-07 10:00:00", "B1"),
            ]
        )
    }
    pairs = _find_symmetric_pairs(sheets, ReconConfig())
    assert len(pairs) == 1
    p = pairs[0]
    assert p["ledger_id"] == "L1"
    assert p["gap_days"] == -2  # ledger is 2 days before bank


def test_find_symmetric_pairs_positive_gap():
    """Bank arrived first, ledger arrived later -> still a pair."""
    from ledger_recon.tracker import _find_symmetric_pairs

    sheets = {
        "2026-01": pd.DataFrame(
            [
                _bank_row("REF1", "1000000", "2026-01-05 10:00:00", "B1"),
                _ledger_row("REF1", "1000000", "2026-01-07 10:00:00", "L1"),
            ]
        )
    }
    pairs = _find_symmetric_pairs(sheets, ReconConfig())
    assert len(pairs) == 1
    assert pairs[0]["gap_days"] == 2


def test_find_symmetric_pairs_rejects_outside_window():
    """Two dates more than recheck_max_days apart do not pair."""
    from ledger_recon.tracker import _find_symmetric_pairs

    cfg = ReconConfig()
    assert cfg.recheck_max_days == 7  # guard against test drifting silently
    sheets = {
        "2026-01": pd.DataFrame(
            [
                _ledger_row("REF1", "1000000", "2026-01-01 10:00:00", "L1"),
                _bank_row("REF1", "1000000", "2026-01-20 10:00:00", "B1"),
            ]
        )
    }
    pairs = _find_symmetric_pairs(sheets, cfg)
    assert pairs == []


def test_find_symmetric_pairs_skips_ambiguous_multiples():
    """Two bank-only + one ledger-only -> pairing would be a guess."""
    from ledger_recon.tracker import _find_symmetric_pairs

    sheets = {
        "2026-01": pd.DataFrame(
            [
                _bank_row("REF1", "1000000", "2026-01-05 10:00:00", "B1"),
                _bank_row("REF1", "1000000", "2026-01-06 10:00:00", "B2"),
                _ledger_row("REF1", "1000000", "2026-01-07 10:00:00", "L1"),
            ]
        )
    }
    pairs = _find_symmetric_pairs(sheets, ReconConfig())
    assert pairs == []


def test_find_symmetric_pairs_skips_amount_mismatch():
    """Rows already classified as amount mismatch are not paired."""
    from ledger_recon.tracker import _find_symmetric_pairs

    bank = _bank_row("REF1", "1000000", "2026-01-05 10:00:00", "B1")
    bank["Amount_Mismatch"] = "True"
    ledger = _ledger_row("REF1", "1000000", "2026-01-06 10:00:00", "L1")
    ledger["Amount_Mismatch"] = "True"

    sheets = {"2026-01": pd.DataFrame([bank, ledger])}
    pairs = _find_symmetric_pairs(sheets, ReconConfig())
    assert pairs == []


def test_find_symmetric_pairs_processes_rows_without_mismatch_column():
    """An old tracker without Amount_Mismatch still pairs normally."""
    from ledger_recon.tracker import _find_symmetric_pairs

    bank = _bank_row("REF1", "1000000", "2026-01-05 10:00:00", "B1")
    ledger = _ledger_row("REF1", "1000000", "2026-01-07 10:00:00", "L1")
    del bank["Amount_Mismatch"]
    del ledger["Amount_Mismatch"]

    sheets = {"2026-01": pd.DataFrame([bank, ledger])}
    pairs = _find_symmetric_pairs(sheets, ReconConfig())
    assert len(pairs) == 1
    # bank 01-05, ledger 01-07 -> ledger minus bank is +2
    assert pairs[0]["gap_days"] == 2


def test_find_symmetric_pairs_only_matches_same_amount():
    """Same reference but different amount -> not a pair (handled elsewhere)."""
    from ledger_recon.tracker import _find_symmetric_pairs

    sheets = {
        "2026-01": pd.DataFrame(
            [
                _bank_row("REF1", "1500000", "2026-01-05 10:00:00", "B1"),
                _ledger_row("REF1", "1000000", "2026-01-06 10:00:00", "L1"),
            ]
        )
    }
    pairs = _find_symmetric_pairs(sheets, ReconConfig())
    assert pairs == []


# --- Pass 0 integration ------------------------------------------------------


def _tracker_pool():
    """Empty ledger pool: forces Pass 1 to do nothing, isolating Pass 0."""
    return pd.DataFrame(columns=["ledger_id", "reference", "amount_minor", "created_at", "ledger_date"])


def test_pass0_resolves_ledger_first_pair_same_month(tmp_path):
    """Ledger exists on Jan 5, bank posts on Jan 7 -> Pass 0 closes both."""
    tracker = tmp_path / "t.xlsx"
    cfg = ReconConfig()

    ledger_pending = pd.DataFrame(
        [
            {
                "reference": "REF1",
                "amount_minor": "1000000",
                "bank_txn_id": None,
                "ledger_id": "L1",
                "bank_posted_at": None,
                "ledger_created_at": "2026-01-05 10:00:00",
            }
        ]
    )
    bank_pending = pd.DataFrame(
        [
            {
                "reference": "REF1",
                "amount_minor": "1000000",
                "bank_txn_id": "B1",
                "ledger_id": None,
                "bank_posted_at": "2026-01-07 10:00:00",
                "ledger_created_at": None,
            }
        ]
    )
    update_tracker(ledger_pending, tracker, cfg)
    update_tracker(bank_pending, tracker, cfg)

    result = recheck_tracker(tracker, _tracker_pool(), cfg)
    assert result["resolved"] == 2
    assert result["still_pending"] == 0

    sheets = pd.read_excel(tracker, sheet_name=None, dtype=str)
    resolved = sheets[SHEET_RESOLVED]
    assert len(resolved) == 2
    assert set(resolved["Resolved_Ledger_ID"]) == {"L1"}
    assert (resolved["Recheck_Status"] == "Resolved").all()


def test_pass0_resolves_across_months(tmp_path):
    """Ledger in January, bank in February -> still one pair."""
    tracker = tmp_path / "t.xlsx"
    cfg = ReconConfig()

    ledger_pending = pd.DataFrame(
        [
            {
                "reference": "REF1",
                "amount_minor": "1000000",
                "bank_txn_id": None,
                "ledger_id": "L1",
                "bank_posted_at": None,
                "ledger_created_at": "2026-01-30 10:00:00",
            }
        ]
    )
    bank_pending = pd.DataFrame(
        [
            {
                "reference": "REF1",
                "amount_minor": "1000000",
                "bank_txn_id": "B1",
                "ledger_id": None,
                "bank_posted_at": "2026-02-02 10:00:00",
                "ledger_created_at": None,
            }
        ]
    )
    update_tracker(ledger_pending, tracker, cfg)
    update_tracker(bank_pending, tracker, cfg)

    result = recheck_tracker(tracker, _tracker_pool(), cfg)
    assert result["resolved"] == 2
    assert result["still_pending"] == 0


def test_pass0_leaves_pair_open_when_gap_exceeds_window(tmp_path):
    """A pair outside recheck_max_days stays pending -- Pass 0 does not guess."""
    tracker = tmp_path / "t.xlsx"
    cfg = ReconConfig()

    ledger_pending = pd.DataFrame(
        [
            {
                "reference": "REF1",
                "amount_minor": "1000000",
                "bank_txn_id": None,
                "ledger_id": "L1",
                "bank_posted_at": None,
                "ledger_created_at": "2026-01-01 10:00:00",
            }
        ]
    )
    bank_pending = pd.DataFrame(
        [
            {
                "reference": "REF1",
                "amount_minor": "1000000",
                "bank_txn_id": "B1",
                "ledger_id": None,
                "bank_posted_at": "2026-01-20 10:00:00",
                "ledger_created_at": None,
            }
        ]
    )
    update_tracker(ledger_pending, tracker, cfg)
    update_tracker(bank_pending, tracker, cfg)

    result = recheck_tracker(tracker, _tracker_pool(), cfg)
    assert result["resolved"] == 0
    assert result["still_pending"] == 2


def test_pass0_does_not_disturb_forward_pairs(tmp_path):
    """Bank first, ledger later: Pass 1 already handles this; Pass 0 must not consume the pool."""
    tracker = tmp_path / "t.xlsx"
    cfg = ReconConfig()

    bank_pending = pd.DataFrame(
        [
            {
                "reference": "REF1",
                "amount_minor": "1000000",
                "bank_txn_id": "B1",
                "ledger_id": None,
                "bank_posted_at": "2026-01-05 10:00:00",
                "ledger_created_at": None,
            }
        ]
    )
    update_tracker(bank_pending, tracker, cfg)

    pool = pd.DataFrame(
        [
            {
                "ledger_id": "L1",
                "reference": "REF1",
                "amount_minor": 1000000,
                "created_at": pd.Timestamp("2026-01-06 10:00:00"),
                "ledger_date": pd.Timestamp("2026-01-06").date(),
            }
        ]
    )
    result = recheck_tracker(tracker, pool, cfg)
    assert result["resolved"] == 1
    assert result["still_pending"] == 0
