# GitHub Releases — copy-paste templates

Create releases at https://github.com/dikaakid/timezone-aware-ledger-recon/releases/new

**Important:** publish in this order — v1.0.0 first, v1.5.0 last. The last
release you publish becomes "Latest" on the repo home page automatically.

For each release:
1. Click "Choose a tag", pick the version below
2. Set the Release title to match
3. Copy the body between the `---` markers below
4. Publish

---

## v1.0.0

**Title:** `v1.0.0 — First stable release`

**Body:**

First stable release of `timezone-aware-ledger-recon`. A companion to
[`ledger-reconciliation-tool`](https://github.com/dikaakid/ledger-reconciliation-tool)
that solves the same matching problem when the three sources
(bank, ledger, verification system) also report timestamps in
different timezones.

### What is in it

- `ledger_recon.tz` normalises every timestamp to WIB at the loader
  boundary. Bank statement and GDS are treated as `Asia/Jakarta`,
  Metabase as UTC.
- `verify_unrecon()` checks every Unrecon row against Metabase using
  `(reference, amount_minor, occurrence)` inside the same WIB day --
  never by reference alone.
- Output columns `bank_posted_at_gmt7` and `ledger_created_at_gmt7`
  carry the explicit `+0700` offset as strings, so Excel does not
  drop it.
- The cumulative tracker keeps open items and closes late-arriving
  pairs on recheck.

75 tests pass on Python 3.10, 3.12, 3.13 on Linux and Windows.

See [CHANGELOG.md](https://github.com/dikaakid/timezone-aware-ledger-recon/blob/main/CHANGELOG.md)
for the full list.

---

## v1.1.0

**Title:** `v1.1.0 — Reject conflicting timezone offsets`

**Body:**

`localize()` used to accept an explicit offset in the raw string and
convert it to WIB regardless of whether the offset matched the source
timezone. A bank row carrying `2026-01-05 00:30:00+00:00` on a WIB
source was silently shifted to 07:30 WIB instead of being flagged.

### Fixed

- An explicit offset that conflicts with the source timezone is now
  rejected to NaT and reported with the reason
  `conflicting offset in posted_at`. An explicit offset that matches
  the source is accepted without shift. Naive rows are still
  interpreted as the source timezone.
- `pd.to_datetime` raises `ValueError` on a column that mixes naive
  and aware rows; `localize()` now falls back to per-element parsing
  so the pipeline never crashes on a mixed column.

### Added

- `conflicting_offset_mask()` in `ledger_recon.tz` identifies rows
  whose explicit offset conflicts with the source, so the loaders can
  report a specific reason.
- `docs/TIMEZONE.md` gains a "What happens to a conflicting offset"
  section with a six-row truth table.

Backward compatible for correct input. Rejects only input that was
silently wrong before. 67 tests pass (was 60).

---

## v1.2.0 [YANKED]

**Title:** `v1.2.0 — Detect amount mismatch pairs [YANKED]`

**Body:**

> **Do not use this release.** The `Amount_Delta` column and the
> `AMOUNT MISMATCH` case label were formatted in minor units
> (`bank 15000000 vs ledger 15500000, delta +500000`) while the
> adjacent `amount` column uses major units. A finance reader would
> read the delta 100x larger than it is. Fixed in v1.2.1. Do not use
> v1.2.0 in production.

### What it did

A bank row and a ledger row that share a reference but disagree on
amount are now recognised as a single mismatch, not two independent
breaks. Both rows receive a distinct case label and two new columns
(`Amount_Mismatch`, `Amount_Delta`). Metabase verification skips
mismatch rows because sending them would only reinforce the wrong
label. 75 tests pass.

### Why it is yanked

The label was 100x too large. The value formatting was fixed in
[v1.2.1](https://github.com/dikaakid/timezone-aware-ledger-recon/releases/tag/v1.2.1).

---

## v1.2.1

**Title:** `v1.2.1 — Fix amount mismatch label scale`

**Body:**

### Fixed

- The `case` label and `Amount_Delta` column for amount mismatches
  were reported in minor units while the adjacent `amount` column
  uses major units. The label now formats both sides and the delta in
  major units with thousands separators and two decimals
  (`AMOUNT MISMATCH (bank 150,000.00 vs ledger 155,000.00, delta
  +5,000.00)`). `Amount_Delta` is in the same scale as `amount`.

### Added

- `Amount_Delta_Minor` column: the signed `ledger - bank` difference
  in exact minor units.

### Changed

- Removed claims from `docs/SAMPLE_OUTPUT.md`, `docs/TIMEZONE.md`,
  and the `pipeline.py` docstring that were never tested
  ("Metabase typically agrees with the bank side", DST support).
- README expected output now reflects the post-1.2.0 Metabase figures.
- CHANGELOG 1.2.0 entry said "was 70"; the previous release had 67.

Upgrade required for anyone who read the label literally. 75 tests
pass.

---

## v1.3.0

**Title:** `v1.3.0 — Split Metabase verification into seven categories`

**Body:**

Metabase verification now classifies Unrecon rows into seven
categories instead of three, depending on which side of the
reconciliation the row belongs to (bank-only vs ledger-only) and how
Metabase describes the key (FINAL, non-FINAL, absent).

### Why

The previous two-way split conflated two very different situations. A
row whose Metabase status is PENDING was labelled "confirmed missing",
the same label as a row that is completely absent from Metabase. A
ledger-only row was also labelled "confirmed missing" even though
Metabase cannot answer that question at all.

### The new categories

Bank-only:
- `Unrecon - pending GDS` (final in Metabase)
- `Unrecon - pending upstream status (bank)` (present, not final)
- `Unrecon - confirmed missing` (absent)

Ledger-only:
- `Unrecon - pending bank posting` (final in Metabase)
- `Unrecon - pending upstream status (ledger)` (present, not final)
- `Unrecon - ledger only, no confirmation` (absent)

Both sides:
- `Unrecon - no reference`

### Other changes

- `verify_unrecon` now requires `bank_txn_id` and `ledger_id` and
  raises if either is missing.
- The per-day log prints three lines split by side.
- `UNRECON_PENDING` and `UNRECON_MISSING` aliases removed.

79 tests pass (was 75).

---

## v1.4.0

**Title:** `v1.4.0 — Close bank-after-ledger pairs`

**Body:**

Closes the bank-after-ledger gap. A payment whose ledger entry came
before its bank entry was never reconciled: the forward-only recheck
required `0 <= ledger_date - bank_date <= recheck_max_days`, so a
negative gap was rejected and the two rows stayed open forever.

### New Pass 0

`_find_symmetric_pairs()` scans the tracker itself for bank-only and
ledger-only pending rows that share the same `(reference,
amount_minor)` and sit within `recheck_max_days` of each other in
either direction. Rows flagged `Amount_Mismatch = True` are skipped.
Rows from a pre-v1.2.0 tracker without that column are still
considered.

### Sample data

A hand-built Pass 0 pair (`L0105-0069` and `B0108-0001`, reference
`8800599988877777`, 200000) is now part of the sample. Running the
pipeline over the sample data resolves 4 rows instead of 2.

### Tests

Eleven new tests: seven unit tests for `_find_symmetric_pairs` and
four integration tests for Pass 0 inside `recheck_tracker`.

90 tests pass (was 79).

---

## v1.4.1

**Title:** `v1.4.1 — Count every Unrecon row in the daily log`

**Body:**

### Fixed

The pipeline log printed four categories of Unrecon but nothing
verified they covered every row. Amount-mismatch rows are excluded
from Metabase verification, so they carried no `Metabase_Status` and
were not counted by the bank/ledger/no-reference lines. On the sample
data the log showed 7 while the header said 9.

The log now prints a fifth line,
`Unrecon (amount mismatch): N`, and `_classify_unrecon_counts()`
raises a `RuntimeError` if the four categories ever fail to sum to
the reported total. A future change that adds a category and forgets
to update the classification will fail loudly instead of silently
under-counting.

92 tests pass (was 90).

---

## v1.5.0

**Title:** `v1.5.0 — Guard against a truncated Metabase export`

**Body:**

The Metabase export is UTC and the reconciliation runs on WIB. An
export cut on UTC midnight drops the 00:00-06:59 WIB window, and a
naive verification would classify every such row as `confirmed
missing` (bank side) or `ledger only, no confirmation` (ledger side)
even though the transaction exists in production.

### Added

- `metabase_export_looks_incomplete()` compares the ledger's early-WIB
  activity against the export. An empty export fires unconditionally
  when the ledger has early activity. A non-empty export fires only
  when the ledger has at least `metabase_min_early_rows` (default 3)
  rows in that window.
- `metabase_min_early_rows` in `ReconConfig` makes the threshold
  configurable.
- Sample data ships this case: `statement_2026-01-09.csv` and
  `ledger_2026-01-09.csv` each have a row at 04:00 WIB, and
  `metabase_2026-01-09.csv` is intentionally empty. The guard fires
  on Day 9 and not on Day 6.

### Fixed

The first version of the guard fired on Day 6 because one ledger row
at 00:00:20 WIB (a cross-midnight twin whose Metabase counterpart is
on Day 1) looked like a truncated window. False positives would have
trained operators to pass `--force` every day, defeating the guard.
The threshold was added in response.

### Other changes

- `reconcile_day` calls the guard after loading Metabase and before
  attaching status. With `--force`, the day proceeds and the warning
  is logged with a `(forced)` suffix.

Seven new tests. 98 tests pass (was 96).