# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

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