# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [1.5.0] - 2026-10-14

### Added
- `metabase_export_looks_incomplete()` in `ledger_recon.metabase`:
  refuses to verify when the Metabase export looks truncated relative
  to the ledger. If the export is cut on UTC midnight, transactions
  from 00:00 to 06:59 WIB fall on the previous UTC day; a naive
  verification would classify every such row as `confirmed missing`
  (bank side) or `ledger only, no confirmation` (ledger side).
- `metabase_min_early_rows` in `ReconConfig` (default 3). A non-empty
  export with no early-WIB rows fires the guard only when the ledger
  has at least this many rows in that window. Below the threshold, a
  single early row may be a cross-midnight twin or an incidental entry
  rather than evidence of a cut export.
- Sample data ships this case: `statement_2026-01-09.csv` and
  `ledger_2026-01-09.csv` each have one row at 04:00 WIB, and
  `metabase_2026-01-09.csv` is intentionally empty. Running the
  pipeline over the sample data prints the guard warning and skips
  verification for that day.
- Seven new tests: four unit tests for the helper (empty file, enough
  early rows with no metabase window, no ledger early rows, both sides
  present), one that a single incidental early row does not fire, and
  one integration test that runs the full sample pipeline and asserts
  the guard fires for Day 9 but not Day 6.

### Changed
- `reconcile_day` calls the guard after loading Metabase and before
  attaching verification status. With `--force`, the day proceeds and
  the warning is logged with a `(forced)` suffix. Without it, the day
  skips Metabase verification and the log prints the reason plus the
  `--force` hint.

### Fixed
- The first version of the guard fired on Day 6 of the sample data
  because one ledger row at 00:00:20 WIB (a cross-midnight twin whose
  Metabase counterpart is on Day 1) looked like a truncated early
  window. False positives would have trained operators to pass
  `--force` every day, defeating the guard. The threshold was added in
  response; Day 6 no longer trips it.

### Compatibility
- Days without an early-WIB ledger row are unaffected. Days where both
  the ledger and Metabase show early-WIB activity are unaffected. The
  guard only changes behaviour when the evidence points to a cut
  export, and `--force` remains an explicit override.

98 tests pass (was 96).

## [1.4.1] - 2026-10-14

### Fixed
- The pipeline log printed four categories of Unrecon but nothing
  checked that they covered every row. Amount-mismatch rows are
  deliberately excluded from Metabase verification, so they have no
  Metabase_Status and were not counted by the bank/ledger/no-reference
  lines. On the sample data the log showed 4 + 3 + 0 = 7 while the
  header said Unrecon 9. Two rows vanished from the operator's view.

  The log now prints a fifth line, `Unrecon (amount mismatch): N`, and
  `_classify_unrecon_counts()` raises a RuntimeError if the four
  categories ever fail to sum to the reported Unrecon total. A future
  change that adds a category and forgets to update the classification
  will fail loudly instead of silently under-counting.

92 tests pass (was 90).

## [1.4.0] - 2026-10-14

### Fixed
- A payment whose ledger entry came before its bank entry was never
  reconciled. Pass 1 iterates bank-only pending rows and requires
  `0 <= ledger_date - bank_date <= recheck_max_days`. When the ledger
  arrived first (for example a transfer recorded on Saturday and posted
  by the bank on Monday), the gap was negative, the forward-only check
  rejected it, and the two rows stayed open forever.

### Added
- `_find_symmetric_pairs()` in `ledger_recon.tracker`: scans the tracker
  itself for bank-only + ledger-only pending rows that share a
  `(reference, amount_minor)` and sit within `recheck_max_days` of each
  other in either direction. Refuses to pair when more than one row on
  either side would make the pairing a guess, when the gap is outside
  the window, or when either row is flagged `Amount_Mismatch = True`.
- Pass 0 in `recheck_tracker` calls the new helper before the existing
  forward-only pass. Any bank-only row it resolves is removed from the
  tracker before Pass 1 sees it, so the two passes cannot double-resolve
  the same row.
- The sample data now ships one Pass 0 pair: ledger row `L0105-0069`
  (reference `8800599988877777`, 200000, 2026-01-05) and bank row
  `B0108-0001` (same reference, 2026-01-08). Running the pipeline
  resolves both on the first recheck, which raises
  `=== Recheck === resolved rows moved` from 2 to 4. The
  `2026-01-08` statement is a new file, `statement_2026-01-08.csv`.
- Eleven new tests: seven unit tests for `_find_symmetric_pairs` and
  four integration tests for Pass 0 inside `recheck_tracker`.

### Changed
- `.gitignore` now blocks `output_*/` and `_*.txt`. Only `*.xlsx` was
  holding the output directories back before, which would have let a
  CSV through; scratch files from review sessions no longer show up as
  untracked.

90 tests pass (was 79).

## [1.3.0] - 2026-10-14

### Changed
- Metabase verification now classifies unreconciled rows into seven
  categories instead of three, depending on which side of the
  reconciliation they belong to (bank-only vs ledger-only) and how
  Metabase describes the key (FINAL, non-FINAL, absent).

  Before:
    Unrecon - pending GDS       present AND final in Metabase
    Unrecon - confirmed missing absent OR not final
    Unrecon - no reference      no reference extracted

  After:
    Bank-only:
      Unrecon - pending GDS                        final in Metabase
      Unrecon - pending upstream status (bank)     present, not final
      Unrecon - confirmed missing                  absent
    Ledger-only:
      Unrecon - pending bank posting               final in Metabase
      Unrecon - pending upstream status (ledger)   present, not final
      Unrecon - ledger only, no confirmation       absent
    Both:
      Unrecon - no reference                       no reference

  The previous two-way split conflated two very different situations:
  a row whose Metabase status is PENDING (transaction exists, not yet
  final) was labelled "confirmed missing", the same label as a row that
  is completely absent. It also labelled ledger-only rows as "confirmed
  missing" when Metabase cannot answer that question at all.

- The pipeline log now prints three lines per day, split by side:
  `Unrecon (bank side)`, `Unrecon (ledger side)`, `Unrecon (no reference)`.
- `verify_unrecon` now requires `bank_txn_id` and `ledger_id` columns on
  its input, to determine which side each row belongs to. It raises a
  clear error if either is missing instead of guessing.

### Added
- Four new constants: `UNRECON_PENDING_UPSTREAM_BANK`,
  `UNRECON_PENDING_UPSTREAM_LEDGER`, `UNRECON_PENDING_BANK_POSTING`,
  `UNRECON_LEDGER_ONLY_UNCONFIRMED`.
- Eight new tests: three bank-side, three ledger-side, one multi-use
  VA, one guard against missing side columns.

### Removed
- `UNRECON_PENDING` and `UNRECON_MISSING` aliases. Use the canonical
  names listed above. The intermediate commit that introduced the new
  constants kept the aliases for backward compatibility; this release
  drops them.

79 tests pass (was 75).

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