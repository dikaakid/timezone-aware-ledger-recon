"""
Loading and validation.

Rules that fix the classic silent-corruption problems:
  * every column is read as TEXT first (no lost leading zeros in account numbers);
  * text is stripped and case-normalised (no false breaks from "ACC-1 " vs "ACC-1");
  * amounts become integer minor units (no float-equality surprises);
  * rows with an unparseable timestamp/amount are REJECTED and reported, never
    silently coerced into NaT/NaN and matched anyway.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from .config import ReconConfig
from .reference import ReferenceExtractor
from .tz import conflicting_offset_mask, localize

STATEMENT_REQUIRED = ["txn_id", "account_no", "posted_at", "description", "direction", "amount"]
LEDGER_REQUIRED = ["ledger_id", "reference", "amount", "created_at", "status"]


def _read(src, rename: dict, side: str, required: list[str]) -> pd.DataFrame:
    if isinstance(src, pd.DataFrame):
        df = src.copy()
    else:
        df = pd.read_csv(src, dtype=str, keep_default_na=False, na_values=[""])
    if rename:
        df = df.rename(columns=rename)
    missing = set(required) - set(df.columns)
    if missing:
        raise ValueError(
            f"{side} is missing required columns: {sorted(missing)}. Expected: {sorted(required)}."
        )
    df = df.astype("string")
    for col in df.columns:
        df[col] = df[col].str.strip()
    return df


def _flagger(index):
    reason = pd.Series("", index=index, dtype="object")

    def flag(mask, text):
        mask = pd.Series(mask, index=index).fillna(False).astype(bool)
        reason.loc[mask & (reason == "")] = text

    return reason, flag


def _to_minor(amount: pd.Series, decimals: int) -> pd.Series:
    return (amount * (10**decimals)).round().astype("int64")


def _object(series: pd.Series) -> pd.Series:
    return series.astype(object).where(series.notna(), None)


def load_statement(src, cfg: ReconConfig):
    """Return (valid_df, rejected_df, meta, extractor) for a bank statement."""
    df = _read(src, cfg.statement_columns, "statement", STATEMENT_REQUIRED)

    placeholder = df["description"].str.lower() == cfg.placeholder_text.lower()
    meta = {"placeholder_rows": int(placeholder.fillna(False).sum())}
    df = df[~placeholder.fillna(False)].reset_index(drop=True)

    df["account_no"] = df["account_no"].str.upper()
    df["direction"] = df["direction"].str.upper()
    posted = localize(df["posted_at"], "statement", cfg.source_timezones)
    amount = pd.to_numeric(df["amount"], errors="coerce")

    reason, flag = _flagger(df.index)
    statement_conflict = conflicting_offset_mask(df["posted_at"], "statement", cfg.source_timezones)
    flag(df["txn_id"].isna(), "missing txn_id")
    flag(posted.isna() & ~statement_conflict, "invalid posted_at")
    flag(statement_conflict, "conflicting offset in posted_at")
    flag(amount.isna(), "invalid amount")
    flag(~df["direction"].isin(["CR", "DB"]), "invalid direction (expected CR or DB)")
    flag(df["txn_id"].duplicated(keep="first") & df["txn_id"].notna(), "duplicate txn_id")

    # Mass rejection by conflicting offset is a configuration mistake, not a
    # file problem. Fail with a message that points at the timezone config
    # instead of letting the run continue with a nearly empty frame and a
    # downstream "statement looks incomplete" warning that misdiagnoses it.
    if len(df) >= cfg.timezone_mismatch_min_rows:
        statement_conflict_pct = 100 * int(statement_conflict.sum()) / len(df)
        if statement_conflict_pct > cfg.timezone_mismatch_threshold_pct:
            raise SystemExit(
                f"source timezone mismatch on statement: "
                f"{int(statement_conflict.sum())} of {len(df)} rows "
                f"({statement_conflict_pct:.0f}%) carry an explicit offset "
                f"that conflicts with the configured source timezone. "
                f"Check source_timezones['statement'], not the file."
            )

    ok = reason == ""
    rejected = df[~ok].copy()
    rejected["reject_reason"] = reason[~ok]

    valid = df[ok].copy().reset_index(drop=True)
    valid["posted_at"] = posted[ok].reset_index(drop=True)
    valid["posted_date"] = valid["posted_at"].dt.date
    valid["amount_minor"] = _to_minor(amount[ok].reset_index(drop=True), cfg.amount_decimals)
    valid["amount"] = valid["amount_minor"] / (10**cfg.amount_decimals)

    extractor = ReferenceExtractor(cfg)
    valid["reference"] = valid["description"].apply(extractor.extract)
    for col in ["txn_id", "account_no", "description", "direction"]:
        valid[col] = _object(valid[col])
    valid["reference"] = _object(valid["reference"])
    return valid, rejected, meta, extractor


def load_ledger(src, cfg: ReconConfig):
    """Return (valid_df, rejected_df, meta) for an internal ledger export."""
    df = _read(src, cfg.ledger_columns, "ledger", LEDGER_REQUIRED)
    if "username" not in df.columns:
        df["username"] = pd.NA

    df["reference"] = df["reference"].str.upper()
    df["status"] = df["status"].str.upper()
    created = localize(df["created_at"], "ledger", cfg.source_timezones)
    amount = pd.to_numeric(df["amount"], errors="coerce")

    reason, flag = _flagger(df.index)
    ledger_conflict = conflicting_offset_mask(df["created_at"], "ledger", cfg.source_timezones)
    flag(df["ledger_id"].isna(), "missing ledger_id")
    flag(df["reference"].isna(), "missing reference")
    flag(created.isna() & ~ledger_conflict, "invalid created_at")
    flag(ledger_conflict, "conflicting offset in created_at")
    flag(amount.isna(), "invalid amount")
    flag(df["ledger_id"].duplicated(keep="first") & df["ledger_id"].notna(), "duplicate ledger_id")

    # Same guard as the statement loader: a mass rejection at this scale is
    # a configuration mistake, not a file problem.
    if len(df) >= cfg.timezone_mismatch_min_rows:
        ledger_conflict_pct = 100 * int(ledger_conflict.sum()) / len(df)
        if ledger_conflict_pct > cfg.timezone_mismatch_threshold_pct:
            raise SystemExit(
                f"source timezone mismatch on ledger: "
                f"{int(ledger_conflict.sum())} of {len(df)} rows "
                f"({ledger_conflict_pct:.0f}%) carry an explicit offset "
                f"that conflicts with the configured source timezone. "
                f"Check source_timezones['ledger'], not the file."
            )

    ok = reason == ""
    rejected = df[~ok].copy()
    rejected["reject_reason"] = reason[~ok]

    valid = df[ok].copy().reset_index(drop=True)
    valid["created_at"] = created[ok].reset_index(drop=True)
    valid["ledger_date"] = valid["created_at"].dt.date
    valid["amount_minor"] = _to_minor(amount[ok].reset_index(drop=True), cfg.amount_decimals)
    valid["amount"] = valid["amount_minor"] / (10**cfg.amount_decimals)

    is_success = valid["status"] == cfg.ledger_success_status.upper()
    meta = {"non_success_rows": int((~is_success).sum())}
    valid = valid[is_success].reset_index(drop=True)
    for col in ["ledger_id", "reference", "status", "username"]:
        valid[col] = _object(valid[col])
    return valid, rejected, meta


def filter_window(ledger: pd.DataFrame, start: date, end: date):
    """Keep only ledger rows inside [start, end]. Returns (kept, dropped_count)."""
    inside = (ledger["ledger_date"] >= start) & (ledger["ledger_date"] <= end)
    return ledger[inside].reset_index(drop=True), int((~inside).sum())
