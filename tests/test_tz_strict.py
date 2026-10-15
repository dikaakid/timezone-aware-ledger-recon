import pandas as pd
import pytest

from ledger_recon.tz import localize


@pytest.mark.parametrize(
    ("values", "nat_positions"),
    [
        (["2026-01-05 10:00:00", "05-01-2026"], [1]),
        (["05/01/2026 10:00:00", "2026-01-05 10:00:00+07:00"], [0]),
        (["2026-01-05 10:00:00+07:00", "2026-01-05 10:00:00+00:00", "05/01/2026 10:00:00"], [1, 2]),
    ],
)
def test_non_iso_rows_are_rejected_on_mixed_columns(values, nat_positions):
    """dd/mm/yyyy must never be re-read as mm/dd/yyyy just because the column is mixed."""
    out = localize(pd.Series(values), "statement")
    for i in range(len(values)):
        assert pd.isna(out.iloc[i]) == (i in nat_positions), (i, values[i], out.iloc[i])


def test_mixed_naive_and_conflicting_offset_keeps_naive_row():
    out = localize(pd.Series(["2026-01-05 00:30:00", "2026-01-05 00:30:00+00:00"]), "statement")
    assert out.iloc[0].hour == 0 and out.iloc[0].day == 5
    assert pd.isna(out.iloc[1])
