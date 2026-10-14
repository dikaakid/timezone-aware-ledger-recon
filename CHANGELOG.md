# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [1.2.1] - 2026-10-14

### Fixed
- The `case` label and `Amount_Delta` column for amount mismatches
  were reported in minor units (`bank 15000000 vs ledger 15500000,
  delta +500000`) while the adjacent `amount` column uses major units.
  A finance reader would read the delta as 100x larger than it is.

  The label now formats both sides and the delta in major units with
  thousands separators and two decimals
  (`AMOUNT MISMATCH (bank 150,000.00 vs ledger 155,000.00, delta
  +5,000.00)`). The value in `Amount_Delta` is now in the same scale
  as `amount` (Float64, major units).

### Added
- `Amount_Delta_Minor` column: the signed `ledger - bank` difference in
  exact minor units. Use `Amount_Delta` for reading, `Amount_Delta_Minor`
  for arithmetic.

### Changed
- `docs/SAMPLE_OUTPUT.md` and `docs/TIMEZONE.md` no longer claim that
  Metabase "typically agrees with the bank side" or that the tool
  supports DST zones. Neither was ever tested; the wording now matches
  what the code actually does.
- README "Expected output" section now reflects the post-1.2.0 Metabase
  figures (`pending_gds 2 | confirmed_missing 4` on 2026-01-05, and
  `1 | 5` on 2026-01-06).
- The heading "Timezone-aware output (v0.2.0+)" was renamed to
  "Timezone-aware output" -- v0.2.0 was never a released version of
  this project.
- CHANGELOG 1.2.0 entry said "75 tests pass (was 70)"; the previous
  release (1.1.0) had 67 tests.

75 tests pass (unchanged).

## [1.2.0] - 2026-10-14

### Added
- `detect_amount_mismatch()` in `ledger_recon.core`: identifies a bank
  row and a ledger row that share a reference but disagree on amount.
- `Amount_Mismatch` (bool) and `Amount_Delta` (Int64, minor units)
  columns in the Line Per Line sheet.
- `amount_mismatch_rows` metric in `summarize()`.
- A distinct `case` label for mismatched pairs:
  `AMOUNT MISMATCH (bank 15000000 vs ledger 15500000, delta +500000)`.
- 8 new tests (3 in test_core.py detection, 3 in test_core.py integration,
  2 in test_metabase.py integration).

### Changed
- Metabase verification now skips rows flagged `Amount_Mismatch = True`.
  Sending them to Metabase would only reinforce the wrong label:
  Metabase agrees with the bank side and would mark the bank row as
  `pending GDS` while the ledger row exists but with a different amount.

### Fixed
- A bank row and a ledger row with the same reference but different
  amounts were previously labelled as two independent breaks
  (`WAITING_LEDGER` and `LEDGER SUCCESS WITHOUT BANK CREDIT`). An
  operator following the first label would wait for a ledger row that
  will never arrive -- the row exists, but with a different amount. The
  pair is now identified as a single amount mismatch and both rows
  carry the same informative label.

### Compatibility
- `Match?` values are unchanged (`Recon` / `Unrecon`).
- `WAITING_LEDGER` is still emitted for bank-only rows that have no
  ledger counterpart at all.
- Existing consumers that filter by `Match?` or by `case` substrings
  (e.g. `"auto-filled"`) are unaffected.

75 tests pass (was 67).

## [1.1.0] - 2026-10-14

### Changed
- `localize()` now rejects an explicit offset that differs from the source
  timezone, instead of silently converting it. A row like
  `2026-01-05 00:30:00+00:00` on a WIB source is written to the
  "Rejected Rows" sheet with the reason `conflicting offset in posted_at`,
  rather than being accepted and shifted by 7 hours.

### Added
- `conflicting_offset_mask()` in `ledger_recon.tz`: identifies rows whose
  explicit offset conflicts with the source, so the loaders can
  distinguish "cannot parse timestamp" from "wrong timezone".
- `docs/TIMEZONE.md` gains a "What happens to a conflicting offset"
  section documenting the six input/source combinations and the
  resulting behaviour.
- Seven new tests covering:
  - conflicting offset rejected (statement, ledger, metabase)
  - matching offset accepted without shift
  - mixed naive/conflicting-aware column handled per element
  - loaders flag conflicting offset with a distinct reject reason

### Fixed
- `pd.to_datetime` raises `ValueError` on a column that mixes naive and
  aware rows instead of returning an object array. `localize()` now falls
  back to per-element parsing in that case, so the pipeline never crashes
  on a mixed column and the reject-and-report contract still holds.

### Compatibility
- Input that was accepted before and was correct (naive rows, or aware
  rows whose offset matches the source) is still accepted unchanged.
- Input that was accepted before but was wrong (aware rows with a
  conflicting offset) is now rejected and reported. This is the intended
  fix, not a regression.

## [1.0.0] - 2026-10-13

### Added
- `ledger_recon.tz` module: per-source timezone normalisation via a single
  `localize()` entry point, plus `to_naive_wib()` and `to_naive_wib_ts()`
  helpers for the Excel write boundary.
- Bank statement and GDS loaders attach `Asia/Jakarta` (WIB) to timestamps
  explicitly, instead of relying on `pd.to_datetime` format inference.
- Metabase loader (`ledger_recon.metabase.load_metabase`): reads the
  verification export in UTC and converts every timestamp to WIB on load.
- Verification layer (`ledger_recon.metabase.verify_unrecon`): every
  `Unrecon` row is checked against Metabase using
  `(reference, amount_minor, occurrence)` within the same WIB day.
- Output columns `bank_posted_at_gmt7` and `ledger_created_at_gmt7`:
  strings formatted with the explicit `+0700` offset, so a finance reader
  never has to guess the timezone of a timestamp.
- `Metabase_Status` classification: `pending GDS` (present and final in
  Metabase) vs `confirmed missing` vs `no reference`.
- CLI `pair` mode gains an optional `--metabase` flag.
- `ReconConfig.source_timezones`, `metabase_final_status`, and
  `metabase_columns` fields.
- `docs/TIMEZONE.md` documenting the design rationale, the failure modes
  the tests protect against, and the deliberate non-goals.

### Changed
- Report and tracker writers call `to_naive_wib()` before writing, because
  Excel cannot store timezone offsets. Timestamps remain tz-aware in memory
  for matching, sorting, and gap calculation.

### Backward compatibility
- When no `metabase_*.csv` file is present for a given day, the pipeline
  produces identical output to the previous release: no `Metabase_Status`
  column, same summary, same row count.
- The two new `_gmt7` columns are added to every report; the existing
  columns are unchanged.

### Inherited from ledger-reconciliation-tool v1.0.0
- Composite-key matching on `(reference, amount, occurrence index)`.
- Exact-length, prefix-validated reference extraction.
- Whole-group ambiguity flag.
- Strict loading: reject-and-report, integer minor units, text-first reading.
- Cumulative tracker with atomic writes and bounded recheck.

This project is a companion to `ledger-reconciliation-tool`. It is not a
replacement: the original tool is simpler and is the recommended choice
when all sources already report in the same timezone.