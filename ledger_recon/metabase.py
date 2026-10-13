"""Metabase loader: the verification layer.

Metabase reports timestamps in UTC (GMT+0). Every timestamp is converted
to WIB (GMT+7) on load, so downstream code never sees a non-WIB
timestamp. The loader is intentionally read-only: it does not verify
anything yet, only produces a clean, normalised DataFrame.

Verification (`verify_unrecon`) lives in a later commit and consumes the
output of `load_metabase`.
"""
from __future__ import annotations

import pandas as pd

from .config import ReconConfig
from .tz import localize

METABASE_REQUIRED = ["trx_id", "reference", "amount", "created_at", "status"]


def load_metabase(src, cfg: ReconConfig) -> pd.DataFrame:
    """Return a validated, WIB-normalised Metabase DataFrame.

    Columns after load:
      - trx_id, reference, status: text, stripped and upper-cased
      - amount_minor: int64, amount in minor units
      - created_at: tz-aware datetime in Asia/Jakarta
      - created_date_wib: date in WIB (the calendar day for daily bucketing)
      - occurrence: 1-based chronological index within (reference, amount_minor),
        computed AFTER timezone normalisation so ordering matches the primary
        reconciliation.
    """
    if isinstance(src, pd.DataFrame):
        df = src.copy()
    else:
        df = pd.read_csv(src, dtype=str, keep_default_na=False, na_values=[""])

    if cfg.metabase_columns:
        df = df.rename(columns=cfg.metabase_columns)

    missing = set(METABASE_REQUIRED) - set(df.columns)
    if missing:
        raise ValueError(
            f"metabase is missing required columns: {sorted(missing)}. "
            f"Expected: {sorted(METABASE_REQUIRED)}."
        )

    df = df.astype("string")
    for col in df.columns:
        df[col] = df[col].str.strip()

    df["reference"] = df["reference"].str.upper()
    df["status"] = df["status"].str.upper()
    df["trx_id"] = df["trx_id"].str.strip()

    df["created_at"] = localize(df["created_at"], "metabase", cfg.source_timezones)
    df["created_date_wib"] = df["created_at"].dt.tz_convert("Asia/Jakarta").dt.date

    amount = pd.to_numeric(df["amount"], errors="coerce")
    df["amount_minor"] = (amount * 10**cfg.amount_decimals).round().astype("Int64")

    df = df.sort_values(["reference", "amount_minor", "created_at"], kind="stable").reset_index(
        drop=True
    )
    df["occurrence"] = df.groupby(["reference", "amount_minor"]).cumcount() + 1

    return df
