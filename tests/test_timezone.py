"""Timezone boundary tests.

Each test proves one failure mode that a naive `pd.to_datetime` call would
have missed. These are the tests that justify the module's existence.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from ledger_recon.tz import TZ_JAKARTA, localize


def test_statement_naive_is_wib_not_converted():
    """Naive bank timestamp stays at the same wall-clock hour."""
    s = pd.Series(["2026-10-12 00:30:00"])
    out = localize(s, "statement")
    assert out.dt.tz == TZ_JAKARTA
    assert out.dt.hour.iloc[0] == 0
    assert out.dt.day.iloc[0] == 12


def test_ledger_naive_is_wib_not_double_converted():
    """GDS is already WIB; a naive timestamp must NOT shift by +7."""
    s = pd.Series(["2026-10-12 00:30:00"])
    out = localize(s, "ledger")
    assert out.dt.hour.iloc[0] == 0  # not 07:30
    assert out.dt.day.iloc[0] == 12


def test_metabase_naive_is_utc_then_converted():
    """Metabase is UTC; 17:30 UTC = 00:30 WIB next day."""
    s = pd.Series(["2026-10-11 17:30:00"])
    out = localize(s, "metabase")
    assert out.dt.tz == TZ_JAKARTA
    assert out.dt.hour.iloc[0] == 0
    assert out.dt.day.iloc[0] == 12


def test_metabase_already_aware_is_converted_not_restamped():
    """If the CSV already carries +00:00, do not double-shift."""
    s = pd.Series(["2026-10-11T17:30:00+00:00"])
    out = localize(s, "metabase")
    assert out.dt.hour.iloc[0] == 0
    assert out.dt.day.iloc[0] == 12


def test_boundary_2359_vs_0001_different_buckets():
    """23:59 WIB and 00:01 WIB must fall in different WIB days."""
    s = pd.Series(["2026-10-11 23:59:00", "2026-10-12 00:01:00"])
    out = localize(s, "statement")
    dates = out.dt.date.tolist()
    assert dates[0] != dates[1]
    assert dates[0] == date(2026, 10, 11)
    assert dates[1] == date(2026, 10, 12)


def test_unparseable_becomes_nat():
    """Bad input becomes NaT, never silently coerced to a valid date."""
    s = pd.Series(["not a date", "", None])
    out = localize(s, "statement")
    assert out.isna().all()


def test_strftime_includes_wib_offset():
    """Aware WIB timestamps format with +0700 so Excel shows the offset."""
    s = pd.Series(["2026-01-05 09:00:00"])
    out = localize(s, "statement")
    formatted = out.dt.strftime("%Y-%m-%d %H:%M:%S %z")
    assert formatted.iloc[0] == "2026-01-05 09:00:00 +0700"


def test_strftime_nat_stays_missing():
    """NaT must stay missing after strftime -- never become a plausible date."""
    s = pd.Series(["not a date"])
    out = localize(s, "statement")
    formatted = out.dt.strftime("%Y-%m-%d %H:%M:%S %z")
    assert pd.isna(formatted.iloc[0])


# --- conflicting offsets ----------------------------------------------------


def test_statement_with_conflicting_offset_is_rejected():
    """A +00:00 offset on a WIB source is a silent corruption -> reject."""
    s = pd.Series(["2026-01-05 00:30:00+00:00"])
    out = localize(s, "statement")
    assert pd.isna(out.iloc[0])


def test_ledger_with_conflicting_offset_is_rejected():
    s = pd.Series(["2026-01-05 00:30:00+00:00"])
    out = localize(s, "ledger")
    assert pd.isna(out.iloc[0])


def test_metabase_with_conflicting_offset_is_rejected():
    """Source is UTC; a +07:00 offset is a conflict, not a convenience."""
    s = pd.Series(["2026-01-05 00:30:00+07:00"])
    out = localize(s, "metabase")
    assert pd.isna(out.iloc[0])


def test_statement_with_matching_offset_is_accepted():
    """An explicit +07:00 on a WIB source matches -> accept, no shift."""
    s = pd.Series(["2026-01-05 00:30:00+07:00"])
    out = localize(s, "statement")
    assert not pd.isna(out.iloc[0])
    assert out.dt.hour.iloc[0] == 0
    assert out.dt.day.iloc[0] == 5


def test_mixed_naive_and_conflicting_aware_in_one_column():
    """Mixed input: naive is accepted, conflicting explicit offset is not."""
    s = pd.Series(["2026-01-05 00:30:00", "2026-01-05 00:30:00+00:00"])
    out = localize(s, "statement")
    assert out.dt.hour.iloc[0] == 0
    assert pd.isna(out.iloc[1])
