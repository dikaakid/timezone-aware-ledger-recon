# Timezone design

This document explains how the tool handles timestamps that arrive in
different timezones, why the conversion happens at a single point, and
what the tool deliberately does *not* try to do.

Read this before changing anything in `ledger_recon/tz.py`,
`loaders.py`, or `metabase.py`.

## The three sources and their native timezones

| Source      | Native timezone | Role                                |
|-------------|-----------------|-------------------------------------|
| Statement   | WIB (GMT+7)     | External truth: what hit the bank   |
| Ledger (GDS)| WIB (GMT+7)     | Internal source of record           |
| Metabase    | UTC (GMT+0)     | Secondary verification of Unrecon   |

Two of the three sources are already in WIB. Only Metabase needs a real
conversion. This matters because the naive fix -- "convert everything" --
would silently corrupt two-thirds of the inputs.

## Why naive parsing is dangerous

A `pd.to_datetime` call on a column of strings does the right thing per
value and the wrong thing across values. The failure is silent:

- Bank reports a transaction at `2026-10-12 00:30 WIB`.
- Metabase reports the same transaction at `2026-10-11 17:30 UTC`
  (the previous UTC day).
- A naive join compares `2026-10-12 00:30` against `2026-10-11 17:30`.
- The day bucket differs. The match either fails (false `Unrecon`) or
  reports a 7-hour time gap that looks like a weak match.
- The daily summary is wrong. Nobody notices until a dispute.

## The rule: normalize once, at the boundary

Every timestamp is normalised exactly once, in the loader that reads it:
statement_.csv -> localize(..., "statement") -> WIB
ledger_.csv -> localize(..., "ledger") -> WIB
metabase_*.csv -> localize(..., "metabase") -> WIB

Downstream code (`core.py`, `tracker.py`, `report.py`) never sees a
non-WIB timestamp. The matching engine itself is timezone-agnostic: it
only compares datetimes that are already in the same zone.

## Why not auto-detect?

An "auto-detect the offset from the string" helper looks convenient and
breaks in production:

- Some rows carry an explicit offset (`+07:00`), others do not.
- A timestamp with no offset is ambiguous: is it local time, or UTC?
- Consistency matters more than cleverness. Two rows from the same
  source must be interpreted the same way, every time.
- A wrong auto-detection is worse than a loud failure.

The source timezone is a property of the **source**, not of the string.
It is declared once in `ReconConfig.source_timezones` and applied
uniformly.

## Why not convert inside the matching loop?

Converting inside `build_line_per_line` would mean:

- Every row is converted `N` times instead of once (once per merge pass).
- The conversion rule is duplicated in three places.
- A test for "23:59 vs 00:01 falls in different days" has to be written
  against the merge, not against the conversion.

Conversion is an `O(N)` operation that belongs at load time. Matching is
`O(N log N)` on already-normalised data.

## Metabase is UTC: confirmed by whom?

The assumption is documented here, not inferred from the data:

- Metabase's own documentation states timestamps are stored in UTC.
- The export used in development shows UTC values in `created_at`.
- If this changes, change `source_timezones["metabase"]` in the config
  (or override it with `--config`). No code change is needed.

If a future export mixes offsets inside the same file, `localize` will
honour the explicit offset and only fall back to the configured timezone
for naive rows. That is the only acceptable hybrid.

## What we deliberately do NOT do

- **No DST handling.** Indonesia has no daylight saving time. A DST-aware
  conversion would add complexity that no current source needs.
- **No auto-detect.** See above.
- **No conversion inside matching.** See above.
- **No timezone math on strings.** Every comparison happens on tz-aware
  `Timestamp` objects, never on formatted strings. The `_gmt7` columns
  in the output are *presentation*, not computation.
- **No assumption that Excel can store the offset.** Excel cannot. That
  is why `_gmt7` columns are strings: they survive the round-trip with
  `+0700` intact, unlike a real datetime column.

## Testing strategy

The boundary tests in `tests/test_timezone.py` are chosen to fail on
plausible mistakes:

- A naive WIB row must not shift by +7 (double conversion).
- A naive UTC row must shift by +7 and may cross the day boundary.
- An already-aware UTC row must be converted, not re-stamped.
- `23:59 WIB` and `00:01 WIB` must fall in different daily buckets.
- An unparseable value must become `NaT`, never a valid date.
- `strftime` on a WIB timestamp must end with `+0700`.
- `strftime` on `NaT` must stay missing, never become a plausible date.

If a future change breaks any of these, the change is wrong, not the
test.