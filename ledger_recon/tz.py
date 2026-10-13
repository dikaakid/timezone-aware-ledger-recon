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
