"""Timezone normalization for multi-source reconciliation.

Rules (learned from production):
  * bank statement (Indonesia) is already WIB (GMT+7) -> attach Asia/Jakarta, never convert
  * GDS export is already WIB (GMT+7)               -> attach Asia/Jakarta, never convert
  * Metabase internal is UTC (GMT+0)                -> attach UTC, convert to Asia/Jakarta
  * ALL timestamps must reach core.py as WIB (GMT+7).

The module is intentionally tiny: it has one job and one test for each
failure mode. Every conversion in the codebase goes through `localize()`.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd

TZ_JAKARTA = ZoneInfo("Asia/Jakarta")
TZ_UTC = ZoneInfo("UTC")

DEFAULT_SOURCE_TZ: dict[str, ZoneInfo] = {
    "statement": TZ_JAKARTA,
    "ledger": TZ_JAKARTA,
    "metabase": TZ_UTC,
}


def _resolve_tz(source: str, mapping: dict | None) -> ZoneInfo:
    """Look up the timezone for a source. Accept ZoneInfo or string."""
    raw = (mapping or DEFAULT_SOURCE_TZ)[source]
    if isinstance(raw, ZoneInfo):
        return raw
    return ZoneInfo(raw)


def localize(series: pd.Series, source: str, mapping: dict | None = None) -> pd.Series:
    """Parse `series` to datetime, attach the source timezone, convert to WIB.

    Naive input is interpreted as the source's native timezone (never UTC by
    default). Aware input is converted, not re-stamped. Output is always
    tz-aware in Asia/Jakarta.
    """
    tz = _resolve_tz(source, mapping)
    dt = pd.to_datetime(series, errors="coerce", format="ISO8601")
    if dt.dt.tz is None:
        dt = dt.dt.tz_localize(tz)
    else:
        dt = dt.dt.tz_convert(tz)
    return dt.dt.tz_convert(TZ_JAKARTA)


def to_naive_wib_ts(x) -> pd.Timestamp:
    """Normalize a single value (string, date, Timestamp) to a naive WIB Timestamp.

    Naive input is assumed to already be WIB; aware input is converted.
    Returns pd.NaT for unparseable input.
    """
    ts = pd.to_datetime(x, errors="coerce")
    if ts is pd.NaT or (hasattr(ts, "__class__") and ts is pd.NaT):
        return pd.NaT
    try:
        if pd.isna(ts):
            return pd.NaT
    except (TypeError, ValueError):
        return pd.NaT
    if ts.tz is not None:
        ts = ts.tz_convert(TZ_JAKARTA).tz_localize(None)
    return ts


def to_naive_wib(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with all tz-aware datetime columns as naive WIB.

    Excel cannot store timezone offsets. All our timestamps are already
    normalised to WIB, so dropping tzinfo preserves the wall-clock value
    a finance user expects while making the DataFrame writable to Excel.
    """
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            try:
                if out[col].dt.tz is not None:
                    out[col] = out[col].dt.tz_convert(TZ_JAKARTA).dt.tz_localize(None)
            except (AttributeError, TypeError):
                pass
    return out
