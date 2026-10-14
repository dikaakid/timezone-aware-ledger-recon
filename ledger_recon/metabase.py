"""Metabase loader and verification layer.

Metabase reports timestamps in UTC (GMT+0). Every timestamp is converted
to WIB (GMT+7) on load, so downstream code never sees a non-WIB value.
"""

from __future__ import annotations

import pandas as pd

from .config import ReconConfig
from .tz import localize

METABASE_REQUIRED = ["trx_id", "reference", "amount", "created_at", "status"]

# Canonical status values. Each one points to a distinct operator action:
# wait for GDS, wait for Metabase to finalize, wait for the bank to post,
# or investigate a real break. The side (bank-only vs ledger-only) is part
# of the label because the same Metabase state implies a different action
# depending on which side is missing.
UNRECON_PENDING_GDS = "Unrecon - pending GDS"
UNRECON_PENDING_UPSTREAM_BANK = "Unrecon - pending upstream status (bank)"
UNRECON_PENDING_UPSTREAM_LEDGER = "Unrecon - pending upstream status (ledger)"
UNRECON_CONFIRMED_MISSING = "Unrecon - confirmed missing"
UNRECON_PENDING_BANK_POSTING = "Unrecon - pending bank posting"
UNRECON_LEDGER_ONLY_UNCONFIRMED = "Unrecon - ledger only, no confirmation"
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
            f"metabase is missing required columns: {sorted(missing)}. Expected: {sorted(METABASE_REQUIRED)}."
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

    df = df.sort_values(["reference", "amount_minor", "created_at"], kind="stable").reset_index(drop=True)
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

    The classification depends on two facts: which side of the
    reconciliation the row belongs to, and how Metabase describes the key.

    Bank-only rows (no ledger counterpart in the merge):
      - present and FINAL in Metabase      -> UNRECON_PENDING_GDS
      - present but not FINAL              -> UNRECON_PENDING_UPSTREAM_BANK
      - absent from Metabase               -> UNRECON_CONFIRMED_MISSING

    Ledger-only rows (no bank counterpart in the merge):
      - present and FINAL in Metabase      -> UNRECON_PENDING_BANK_POSTING
      - present but not FINAL              -> UNRECON_PENDING_UPSTREAM_LEDGER
      - absent from Metabase               -> UNRECON_LEDGER_ONLY_UNCONFIRMED

    In both cases, a missing reference yields UNRECON_NO_REF.

    The caller must supply bank_txn_id and ledger_id columns so the side
    can be determined. Rows with both or neither set are not expected on
    input; the function raises on a missing column rather than guessing.
    """
    out = unrecon_rows.copy()
    if out.empty:
        out["Metabase_Status"] = pd.Series(dtype="string")
        return out

    for col in ("bank_txn_id", "ledger_id"):
        if col not in out.columns:
            raise ValueError(
                f"verify_unrecon requires a {col!r} column to determine which "
                f"side of the reconciliation each row belongs to."
            )

    mb = metabase.copy()
    status = mb["status"].str.upper()
    final_status = cfg.metabase_final_status.upper()

    mb_final = mb[status == final_status]
    mb_nonfinal = mb[status != final_status]

    final_keys = set(
        zip(
            mb_final["reference"],
            mb_final["amount_minor"],
            mb_final["occurrence"],
            strict=True,
        )
    )
    nonfinal_keys = set(
        zip(
            mb_nonfinal["reference"],
            mb_nonfinal["amount_minor"],
            mb_nonfinal["occurrence"],
            strict=True,
        )
    )

    statuses: list[str] = []
    for _, row in out.iterrows():
        ref = row.get("reference")
        if ref is None or pd.isna(ref):
            statuses.append(UNRECON_NO_REF)
            continue

        is_bank_side = pd.notna(row.get("bank_txn_id"))
        key = (str(ref), int(row["amount_minor"]), int(row.get("occurrence", 1)))

        if key in final_keys:
            statuses.append(UNRECON_PENDING_GDS if is_bank_side else UNRECON_PENDING_BANK_POSTING)
        elif key in nonfinal_keys:
            statuses.append(
                UNRECON_PENDING_UPSTREAM_BANK if is_bank_side else UNRECON_PENDING_UPSTREAM_LEDGER
            )
        else:
            statuses.append(UNRECON_CONFIRMED_MISSING if is_bank_side else UNRECON_LEDGER_ONLY_UNCONFIRMED)

    out["Metabase_Status"] = statuses
    return out
