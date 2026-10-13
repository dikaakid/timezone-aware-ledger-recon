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
    UNRECON_MISSING,
    UNRECON_NO_REF,
    UNRECON_PENDING,
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


def _unrecon(reference="8800112345678901", amount_minor=1_000_000, occurrence=1):
    """One unreconciled row, defaulting to the same amount as _df()."""
    return pd.DataFrame({
        "reference": [reference],
        "amount_minor": [amount_minor],
        "occurrence": [occurrence],
    })


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

def test_verify_marks_pending_when_present_in_metabase():
    mb = load_metabase(_df(status="FINAL"), ReconConfig())
    out = verify_unrecon(_unrecon(), mb, ReconConfig())
    assert out["Metabase_Status"].iloc[0] == UNRECON_PENDING


def test_verify_marks_missing_when_absent():
    mb = load_metabase(_df(status="FINAL"), ReconConfig())
    out = verify_unrecon(_unrecon(reference="9999999999999999"), mb, ReconConfig())
    assert out["Metabase_Status"].iloc[0] == UNRECON_MISSING


def test_verify_marks_missing_when_status_not_final():
    mb = load_metabase(_df(status="PENDING"), ReconConfig())
    out = verify_unrecon(_unrecon(), mb, ReconConfig())
    assert out["Metabase_Status"].iloc[0] == UNRECON_MISSING


def test_verify_multi_use_va_uses_occurrence_not_reference_alone():
    """Two rows same reference+amount, only occurrence=1 exists in Metabase."""
    unrecon = pd.DataFrame({
        "reference": ["8800112345678901", "8800112345678901"],
        "amount_minor": [1_000_000, 1_000_000],
        "occurrence": [1, 2],
    })
    mb = load_metabase(_df(status="FINAL"), ReconConfig())
    out = verify_unrecon(unrecon, mb, ReconConfig())
    assert out["Metabase_Status"].tolist() == [UNRECON_PENDING, UNRECON_MISSING]


def test_verify_no_reference_flagged():
    mb = load_metabase(_df(status="FINAL"), ReconConfig())
    out = verify_unrecon(_unrecon(reference=None), mb, ReconConfig())
    assert out["Metabase_Status"].iloc[0] == UNRECON_NO_REF


def test_verify_empty_unrecon_returns_empty():
    mb = load_metabase(_df(), ReconConfig())
    out = verify_unrecon(pd.DataFrame(), mb, ReconConfig())
    assert out.empty
    assert "Metabase_Status" in out.columns
