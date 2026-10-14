"""Timezone normalization for multi-source reconciliation.

Rules (learned from production):
  * bank statement (Indonesia) is already WIB (GMT+7) -> attach Asia/Jakarta, never convert
  * GDS export is already WIB (GMT+7)               -> attach Asia/Jakarta, never convert
  * Metabase internal is UTC (GMT+0)                -> attach UTC, convert to Asia/Jakarta
  * ALL timestamps must reach core.py as WIB (GMT+7).

An explicit offset in the raw string is trusted only when it matches the
source timezone's offset at that instant. A conflicting offset is rejected
to NaT, consistent with the reject-and-report doctrine used elsewhere:
source timezone is a property of the source, not of the string.

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


def _accept_aware(ts: pd.Timestamp, tz: ZoneInfo) -> pd.Timestamp | None:
    """Return ts converted to WIB if its offset matches the source, else None.

    Comparing offsets at the actual instant (not at a fixed reference) so
    the check stays correct if a future source uses a DST zone. WIB and UTC
    do not use DST; the tool does not claim DST support.
    """
    if ts.tz is None:
        return ts.tz_localize(tz).tz_convert(TZ_JAKARTA)
    if ts.utcoffset() == ts.tz_convert(tz).utcoffset():
        return ts.tz_convert(TZ_JAKARTA)
    return None


def _localize_slow(series: pd.Series, tz: ZoneInfo) -> pd.Series:
    """Per-element fallback for mixed naive/aware or mixed-offset input.

    pandas raises ValueError on these inputs instead of returning an object
    array, so we cannot rely on the fast path. Reading values one at a time
    is slower but keeps the reject-and-report contract intact.
    """
    result = []
    for x in series:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            result.append(pd.NaT)
            continue
        try:
            ts = pd.Timestamp(x)
        except (ValueError, TypeError):
            result.append(pd.NaT)
            continue
        if pd.isna(ts):
            result.append(pd.NaT)
            continue
        converted = _accept_aware(ts, tz)
        result.append(converted if converted is not None else pd.NaT)
    return pd.Series(result, index=series.index, dtype="datetime64[ns, Asia/Jakarta]")


def localize(series: pd.Series, source: str, mapping: dict | None = None) -> pd.Series:
    """Parse `series` to datetime, attach the source timezone, convert to WIB.

    Naive input is interpreted as the source's native timezone (never UTC by
    default). Aware input is accepted only when its explicit offset matches
    the source's offset at that instant; a conflicting offset is rejected to
    NaT. Output is always tz-aware in Asia/Jakarta.

    On a column that mixes naive and aware rows (or rows with different
    offsets), pandas refuses to parse the column as a whole and raises
    ValueError. In that case we fall back to per-element parsing so the
    reject-and-report contract still holds: naive rows are accepted, aware
    rows with the wrong offset become NaT, and the pipeline never crashes.
    """
    tz = _resolve_tz(source, mapping)

    try:
        raw = pd.to_datetime(series, errors="coerce", format="ISO8601")
    except ValueError:
        # pandas cannot infer a single tz for the whole column.
        return _localize_slow(series, tz)

    # Fast path 1: all naive (or all NaT)
    if pd.api.types.is_datetime64_any_dtype(raw) and raw.dt.tz is None:
        return raw.dt.tz_localize(tz).dt.tz_convert(TZ_JAKARTA)

    # Fast path 2: all aware, pandas inferred a single tz
    if pd.api.types.is_datetime64_any_dtype(raw) and raw.dt.tz is not None:
        valid = raw.dropna()
        if not valid.empty:
            sample = valid.iloc[0]
            if sample.utcoffset() != sample.tz_convert(tz).utcoffset():
                return pd.Series(
                    [pd.NaT] * len(series),
                    index=series.index,
                    dtype="datetime64[ns, Asia/Jakarta]",
                )
        return raw.dt.tz_convert(TZ_JAKARTA)

    # Fallback for object dtype without a pandas exception
    return _localize_slow(series, tz)


def conflicting_offset_mask(series: pd.Series, source: str, mapping: dict | None = None) -> pd.Series:
    """Return True for rows whose explicit offset conflicts with the source.

    Used by the loaders to distinguish "cannot parse timestamp" from
    "timestamp carries the wrong timezone". Both produce NaT from
    `localize`, but they call for different user actions: the first means
    the CSV is malformed, the second means the CSV was exported with the
    wrong timezone.

    A row without an explicit offset is not a conflict; a row with a
    matching offset is not a conflict. Only aware rows whose offset
    differs from the source's offset at that instant are flagged.
    """
    tz = _resolve_tz(source, mapping)
    result = []
    for x in series:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            result.append(False)
            continue
        try:
            ts = pd.Timestamp(x)
        except (ValueError, TypeError):
            result.append(False)
            continue
        if pd.isna(ts) or ts.tz is None:
            result.append(False)
            continue
        result.append(ts.utcoffset() != ts.tz_convert(tz).utcoffset())
    return pd.Series(result, index=series.index, dtype="bool")


def to_naive_wib_ts(x) -> pd.Timestamp:
    """Normalize a single value (string, date, Timestamp) to a naive WIB Timestamp.

    Naive input is assumed to already be WIB; aware input is converted.
    Returns pd.NaT for unparseable input.
    """
    ts = pd.to_datetime(x, errors="coerce")
    if ts is pd.NaT:
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
