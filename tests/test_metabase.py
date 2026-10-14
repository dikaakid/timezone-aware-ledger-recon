"""Metabase loader and verification tests.

Verifies that UTC timestamps are converted to WIB on load, that the
occurrence index is computed after conversion, that the loader fails
loudly on missing columns, and that verify_unrecon classifies rows by
the (reference, amount_minor, occurrence) key.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from ledger_recon.config import ReconConfig
from ledger_recon.metabase import (
    UNRECON_CONFIRMED_MISSING,
    UNRECON_LEDGER_ONLY_UNCONFIRMED,
    UNRECON_NO_REF,
    UNRECON_PENDING_BANK_POSTING,
    UNRECON_PENDING_GDS,
    UNRECON_PENDING_UPSTREAM_BANK,
    UNRECON_PENDING_UPSTREAM_LEDGER,
    load_metabase,
    verify_unrecon,
)


def _df(**overrides):
    """One Metabase row: 10000.00 -> 1_000_000 minor units."""
    base = {
        "trx_id": ["MB1"],
        "reference": ["8800112345678901"],
        "amount": ["10000"],
        "created_at": ["2026-10-11 17:30:00"],  # UTC -> 2026-10-12 00:30 WIB
        "status": ["FINAL"],
    }
    base.update(overrides)
    return pd.DataFrame(base)


def _unrecon(
    reference="8800112345678901",
    amount_minor=1_000_000,
    occurrence=1,
    side="bank",
):
    """One unreconciled row, defaulting to the same amount as _df().

    side="bank" fills bank_txn_id and leaves ledger_id empty, side="ledger"
    does the opposite. verify_unrecon needs one of the two set to decide
    which classification path to take.
    """
    return pd.DataFrame(
        {
            "reference": [reference],
            "amount_minor": [amount_minor],
            "occurrence": [occurrence],
            "bank_txn_id": ["B1"] if side == "bank" else [None],
            "ledger_id": [None] if side == "bank" else ["L1"],
        }
    )


# --- loader tests -----------------------------------------------------------


def test_utc_timestamp_converted_to_wib():
    out = load_metabase(_df(), ReconConfig())
    assert out["created_at"].dt.hour.iloc[0] == 0
    assert out["created_at"].dt.day.iloc[0] == 12
    assert out["created_date_wib"].iloc[0] == date(2026, 10, 12)


def test_occurrence_computed_after_conversion():
    df = pd.concat([_df(), _df(trx_id="MB2")], ignore_index=True)
    out = load_metabase(df, ReconConfig())
    assert sorted(out["occurrence"].tolist()) == [1, 2]


def test_missing_required_column_raises():
    df = _df().drop(columns=["status"])
    with pytest.raises(ValueError, match="missing required columns"):
        load_metabase(df, ReconConfig())


def test_column_rename_via_config():
    df = _df().rename(columns={"trx_id": "id", "reference": "va"})
    cfg = ReconConfig(metabase_columns={"id": "trx_id", "va": "reference"})
    out = load_metabase(df, cfg)
    assert "trx_id" in out.columns
    assert "reference" in out.columns
    assert out["trx_id"].iloc[0] == "MB1"


# --- verification tests -----------------------------------------------------


def test_verify_bank_final_is_pending_gds():
    mb = load_metabase(_df(status="FINAL"), ReconConfig())
    out = verify_unrecon(_unrecon(side="bank"), mb, ReconConfig())
    assert out["Metabase_Status"].iloc[0] == UNRECON_PENDING_GDS


def test_verify_bank_absent_is_confirmed_missing():
    mb = load_metabase(_df(status="FINAL"), ReconConfig())
    out = verify_unrecon(_unrecon(reference="9999999999999999", side="bank"), mb, ReconConfig())
    assert out["Metabase_Status"].iloc[0] == UNRECON_CONFIRMED_MISSING


def test_verify_bank_nonfinal_is_pending_upstream_bank():
    mb = load_metabase(_df(status="PENDING"), ReconConfig())
    out = verify_unrecon(_unrecon(side="bank"), mb, ReconConfig())
    assert out["Metabase_Status"].iloc[0] == UNRECON_PENDING_UPSTREAM_BANK


def test_verify_ledger_final_is_pending_bank_posting():
    mb = load_metabase(_df(status="FINAL"), ReconConfig())
    out = verify_unrecon(_unrecon(side="ledger"), mb, ReconConfig())
    assert out["Metabase_Status"].iloc[0] == UNRECON_PENDING_BANK_POSTING


def test_verify_ledger_nonfinal_is_pending_upstream_ledger():
    mb = load_metabase(_df(status="PENDING"), ReconConfig())
    out = verify_unrecon(_unrecon(side="ledger"), mb, ReconConfig())
    assert out["Metabase_Status"].iloc[0] == UNRECON_PENDING_UPSTREAM_LEDGER


def test_verify_ledger_absent_is_ledger_only_unconfirmed():
    mb = load_metabase(_df(status="FINAL"), ReconConfig())
    out = verify_unrecon(_unrecon(reference="9999999999999999", side="ledger"), mb, ReconConfig())
    assert out["Metabase_Status"].iloc[0] == UNRECON_LEDGER_ONLY_UNCONFIRMED


def test_verify_multi_use_va_uses_occurrence_not_reference_alone():
    """Two rows same reference+amount, only occurrence=1 exists in Metabase."""
    unrecon = pd.DataFrame(
        {
            "reference": ["8800112345678901", "8800112345678901"],
            "amount_minor": [1_000_000, 1_000_000],
            "occurrence": [1, 2],
            "bank_txn_id": ["B1", "B2"],
            "ledger_id": [None, None],
        }
    )
    mb = load_metabase(_df(status="FINAL"), ReconConfig())
    out = verify_unrecon(unrecon, mb, ReconConfig())
    assert out["Metabase_Status"].tolist() == [
        UNRECON_PENDING_GDS,
        UNRECON_CONFIRMED_MISSING,
    ]


def test_verify_raises_without_side_columns():
    """The function must refuse input it cannot classify by side."""
    mb = load_metabase(_df(status="FINAL"), ReconConfig())
    bad = pd.DataFrame(
        {
            "reference": ["8800112345678901"],
            "amount_minor": [1_000_000],
            "occurrence": [1],
        }
    )
    with pytest.raises(ValueError, match="bank_txn_id"):
        verify_unrecon(bad, mb, ReconConfig())


def test_verify_no_reference_flagged():
    mb = load_metabase(_df(status="FINAL"), ReconConfig())
    out = verify_unrecon(_unrecon(reference=None), mb, ReconConfig())
    assert out["Metabase_Status"].iloc[0] == UNRECON_NO_REF


def test_verify_empty_unrecon_returns_empty():
    mb = load_metabase(_df(), ReconConfig())
    out = verify_unrecon(pd.DataFrame(), mb, ReconConfig())
    assert out.empty
    assert "Metabase_Status" in out.columns


# --- _attach_metabase_status integration ------------------------------------


def test_attach_metabase_status_skips_amount_mismatch_rows():
    """Rows already flagged as amount mismatch are not sent to Metabase.

    Verifying them would only reinforce the wrong label: Metabase agrees
    with the bank side and would mark the bank row as pending GDS, while
    the ledger row exists but with a different amount.
    """
    from ledger_recon.pipeline import _attach_metabase_status

    cfg = ReconConfig()
    lpl = pd.DataFrame(
        [
            {
                "reference": "8800112345678901",
                "amount_minor": 15_000_000,
                "occurrence": 1,
                "Match?": "Unrecon",
                "Amount_Mismatch": True,
            },
            {
                "reference": "8800112345678901",
                "amount_minor": 15_500_000,
                "occurrence": 1,
                "Match?": "Unrecon",
                "Amount_Mismatch": True,
            },
        ]
    )
    mb = load_metabase(_df(status="FINAL"), cfg)

    result = _attach_metabase_status(lpl, mb, cfg)
    assert pd.isna(result["Metabase_Status"].iloc[0])
    assert pd.isna(result["Metabase_Status"].iloc[1])


def test_attach_metabase_status_still_verifies_normal_unrecon():
    """A non-mismatch Unrecon row is still checked against Metabase."""
    from ledger_recon.pipeline import _attach_metabase_status

    cfg = ReconConfig()
    lpl = pd.DataFrame(
        [
            {
                "reference": "8800112345678901",
                "amount_minor": 1_000_000,
                "occurrence": 1,
                "Match?": "Unrecon",
                "Amount_Mismatch": False,
                "bank_txn_id": "B1",
                "ledger_id": None,
            },
        ]
    )
    mb = load_metabase(_df(status="FINAL"), cfg)

    result = _attach_metabase_status(lpl, mb, cfg)
    assert result["Metabase_Status"].iloc[0] == UNRECON_PENDING_GDS


# --- metabase_export_looks_incomplete unit tests ----------------------------


def _ledger_frame_for_guard(rows):
    """Build a ledger frame with the columns the guard reads."""
    return pd.DataFrame(rows)


def _metabase_frame_for_guard(rows=None, columns=None):
    """Build a metabase frame for the guard tests.

    Accepts either a list of dicts (rows) or a list of column names
    (columns), whichever the caller passes. The guard reads only
    created_at; when rows are given, the frame carries whatever columns
    the dicts define.
    """
    if columns is not None:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows or [])


def test_guard_fires_when_ledger_early_and_metabase_empty():
    """Ledger early WIB row, metabase export has no rows at all."""
    from datetime import date

    from ledger_recon.metabase import metabase_export_looks_incomplete

    ledger = _ledger_frame_for_guard(
        [
            {
                "ledger_date": date(2026, 1, 5),
                "created_at": pd.Timestamp("2026-01-05 04:00:00", tz="Asia/Jakarta"),
            }
        ]
    )
    metabase = _metabase_frame_for_guard(columns=["created_at"])
    incomplete, reasons = metabase_export_looks_incomplete(metabase, ledger, date(2026, 1, 5), ReconConfig())
    assert incomplete is True
    assert any("00:00-06:59" in r for r in reasons)


def test_guard_fires_when_enough_ledger_early_rows_and_none_in_metabase():
    """Five ledger early rows and zero in the metabase window -> truncation."""
    from datetime import date

    from ledger_recon.metabase import metabase_export_looks_incomplete

    ledger = _ledger_frame_for_guard(
        [
            {
                "ledger_date": date(2026, 1, 5),
                "created_at": pd.Timestamp(f"2026-01-05 0{h}:00:00", tz="Asia/Jakarta"),
            }
            for h in range(1, 6)
        ]
    )
    metabase = _metabase_frame_for_guard(
        [
            {
                "created_at": pd.Timestamp("2026-01-05 10:00:00", tz="Asia/Jakarta"),
            }
        ]
    )
    incomplete, reasons = metabase_export_looks_incomplete(metabase, ledger, date(2026, 1, 5), ReconConfig())
    assert incomplete is True
    assert any("0 rows in that window" in r for r in reasons)


def test_guard_does_not_fire_when_ledger_has_no_early_rows():
    """Ledger has no transactions in the early window -- nothing to check."""
    from datetime import date

    from ledger_recon.metabase import metabase_export_looks_incomplete

    ledger = _ledger_frame_for_guard(
        [
            {
                "ledger_date": date(2026, 1, 5),
                "created_at": pd.Timestamp("2026-01-05 10:00:00", tz="Asia/Jakarta"),
            }
        ]
    )
    metabase = _metabase_frame_for_guard(columns=["created_at"])
    incomplete, reasons = metabase_export_looks_incomplete(metabase, ledger, date(2026, 1, 5), ReconConfig())
    assert incomplete is False
    assert reasons == []


def test_guard_does_not_fire_when_both_have_early_rows():
    """Ledger early row and metabase early row -- export looks complete."""
    from datetime import date

    from ledger_recon.metabase import metabase_export_looks_incomplete

    ledger = _ledger_frame_for_guard(
        [
            {
                "ledger_date": date(2026, 1, 5),
                "created_at": pd.Timestamp("2026-01-05 04:00:00", tz="Asia/Jakarta"),
            }
        ]
    )
    metabase = _metabase_frame_for_guard(
        [
            {
                "created_at": pd.Timestamp("2026-01-05 04:30:00", tz="Asia/Jakarta"),
            }
        ]
    )
    incomplete, reasons = metabase_export_looks_incomplete(metabase, ledger, date(2026, 1, 5), ReconConfig())
    assert incomplete is False
    assert reasons == []


def test_guard_does_not_fire_on_one_incidental_early_row():
    """A single ledger early row with no metabase window is not a truncation.

    Sample data has a cross-midnight twin: one ledger row at 00:00:20 WIB
    whose Metabase counterpart lives on the previous day. This must not
    trigger the guard, or operators would see the warning every day and
    learn to ignore it.
    """
    from datetime import date

    from ledger_recon.metabase import metabase_export_looks_incomplete

    ledger = _ledger_frame_for_guard(
        [
            {
                "ledger_date": date(2026, 1, 6),
                "created_at": pd.Timestamp("2026-01-06 00:00:20", tz="Asia/Jakarta"),
            }
        ]
    )
    metabase = _metabase_frame_for_guard(
        [
            {
                "created_at": pd.Timestamp("2026-01-06 13:00:00", tz="Asia/Jakarta"),
            }
        ]
    )
    incomplete, reasons = metabase_export_looks_incomplete(metabase, ledger, date(2026, 1, 6), ReconConfig())
    assert incomplete is False
    assert reasons == []


# --- _classify_mismatch_verdict unit tests ----------------------------------


def _mb_row(reference, occurrence, amount_minor):
    """Minimal Metabase row for the verdict tests (three columns only)."""
    return {
        "reference": reference,
        "occurrence": occurrence,
        "amount_minor": amount_minor,
    }


def test_classify_verdict_bank_matches_metabase():
    """Metabase holds the bank amount -> the ledger recorded wrong figures."""
    from ledger_recon.metabase import (
        VERDICT_BANK_MATCHES,
        _classify_mismatch_verdict,
    )

    mb = pd.DataFrame([_mb_row("R1", 1, 15_000_000)])
    v = _classify_mismatch_verdict(
        reference="R1",
        occurrence=1,
        bank_minor=15_000_000,
        ledger_minor=15_500_000,
        metabase=mb,
    )
    assert v == VERDICT_BANK_MATCHES


def test_classify_verdict_ledger_matches_metabase():
    """Metabase holds the ledger amount -> the bank received something else."""
    from ledger_recon.metabase import (
        VERDICT_LEDGER_MATCHES,
        _classify_mismatch_verdict,
    )

    mb = pd.DataFrame([_mb_row("R1", 1, 15_500_000)])
    v = _classify_mismatch_verdict(
        reference="R1",
        occurrence=1,
        bank_minor=15_000_000,
        ledger_minor=15_500_000,
        metabase=mb,
    )
    assert v == VERDICT_LEDGER_MATCHES


def test_classify_verdict_both_differ():
    """Metabase has the reference but neither amount -> investigate."""
    from ledger_recon.metabase import (
        VERDICT_BOTH_DIFFER,
        _classify_mismatch_verdict,
    )

    mb = pd.DataFrame([_mb_row("R1", 1, 14_000_000)])
    v = _classify_mismatch_verdict(
        reference="R1",
        occurrence=1,
        bank_minor=15_000_000,
        ledger_minor=15_500_000,
        metabase=mb,
    )
    assert v == VERDICT_BOTH_DIFFER


def test_classify_verdict_both_match_metabase():
    """Metabase holds both amounts -> investigate a duplicate."""
    from ledger_recon.metabase import (
        VERDICT_BOTH_MATCH,
        _classify_mismatch_verdict,
    )

    mb = pd.DataFrame(
        [
            _mb_row("R1", 1, 15_000_000),
            _mb_row("R1", 1, 15_500_000),
        ]
    )
    v = _classify_mismatch_verdict(
        reference="R1",
        occurrence=1,
        bank_minor=15_000_000,
        ledger_minor=15_500_000,
        metabase=mb,
    )
    assert v == VERDICT_BOTH_MATCH


def test_classify_verdict_no_metabase_data():
    """Metabase has no row with this reference -> nothing to arbitrate."""
    from ledger_recon.metabase import (
        VERDICT_NO_DATA,
        _classify_mismatch_verdict,
    )

    mb = pd.DataFrame([_mb_row("R2", 1, 15_000_000)])
    v = _classify_mismatch_verdict(
        reference="R1",
        occurrence=1,
        bank_minor=15_000_000,
        ledger_minor=15_500_000,
        metabase=mb,
    )
    assert v == VERDICT_NO_DATA
