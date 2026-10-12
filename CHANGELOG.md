# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-12

### Added
- Initial fork from `ledger-reconciliation-tool` v1.0.0.
- Timezone normalization at the loader boundary (`ledger_recon.tz`):
  bank and ledger sources are interpreted as WIB (GMT+7); Metabase is
  interpreted as UTC and converted to WIB.
- Metabase verification layer: `Unrecon` rows are checked against a
  second system using `(reference, amount_minor, occurrence)` within
  the same WIB day.
- Output columns `bank_posted_at_gmt7` and `ledger_created_at_gmt7`.
- `Metabase_Status` distinguishes "pending GDS" from "confirmed missing".

### Inherited from ledger-reconciliation-tool v1.0.0
- Composite-key matching on `(reference, amount, occurrence index)`.
- Exact-length, prefix-validated reference extraction.
- Whole-group ambiguity flag.
- Strict loading: reject-and-report, integer minor units, text-first reading.
- Cumulative tracker with atomic writes and bounded recheck.

This project is a companion to `ledger-reconciliation-tool`. It is not a
replacement: the original tool is simpler and is the recommended choice
when all sources already report in the same timezone.