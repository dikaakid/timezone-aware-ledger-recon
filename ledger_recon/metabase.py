"""Metabase loader and verification layer.

Metabase reports timestamps in UTC (GMT+0). Every timestamp is converted
to WIB (GMT+7) on load, so downstream code never sees a non-WIB value.
"""
from __future__ import annotations

import pandas as pd

from .config import ReconConfig
from .tz import localize

METABASE_REQUIRED = ["trx_id", "reference", "amount", "created_at", "status"]

UNRECON_PENDING = "Unrecon - pending GDS"
UNRECON_MISSING = "Unrecon - confirmed missing"
UNRECON_NO_REF = "Unrecon - no reference"


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


def verify_unrecon(
    unrecon_rows: pd.DataFrame,
    metabase: pd.DataFrame,
    cfg: ReconConfig,
) -> pd.DataFrame:
    """Return unrecon_rows with a Metabase_Status column.

    Join key: (reference, amount_minor, occurrence) within the same WIB day.
    Never by reference alone, because a reference can be reused many times
    in a single day (multi-use virtual accounts).

    Status values:
      - UNRECON_PENDING: present and final in Metabase -> data pipeline lag
      - UNRECON_MISSING: absent or not final            -> real break
      - UNRECON_NO_REF:  no reference was extractable
    """
    out = unrecon_rows.copy()
    if out.empty:
        out["Metabase_Status"] = pd.Series(dtype="string")
        return out

    mb = metabase.copy()
    mb_final = mb[mb["status"].str.upper() == cfg.metabase_final_status.upper()]
    mb_keys = set(
        zip(mb_final["reference"], mb_final["amount_minor"], mb_final["occurrence"], strict=True)
    )

    statuses: list[str] = []
    for _, row in out.iterrows():
        ref = row.get("reference")
        if ref is None or pd.isna(ref):
            statuses.append(UNRECON_NO_REF)
            continue
        key = (str(ref), int(row["amount_minor"]), int(row.get("occurrence", 1)))
        statuses.append(UNRECON_PENDING if key in mb_keys else UNRECON_MISSING)

    out["Metabase_Status"] = statuses
    return out
