"""Metabase loader tests.

Verifies that UTC timestamps are converted to WIB on load, that the
occurrence index is computed after conversion, and that the loader
fails loudly on missing columns or bad config.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from ledger_recon.config import ReconConfig
from ledger_recon.metabase import load_metabase


def _df(**overrides):
    base = {
        "trx_id": ["MB1"],
        "reference": ["8800112345678901"],
        "amount": ["10000"],
        "created_at": ["2026-10-11 17:30:00"],  # UTC -> 2026-10-12 00:30 WIB
        "status": ["FINAL"],
    }
    base.update(overrides)
    return pd.DataFrame(base)


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
