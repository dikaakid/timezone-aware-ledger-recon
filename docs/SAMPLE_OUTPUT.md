# Sample output

Everything below is produced from the **synthetic** data in `examples/dummy_data/` by:

    python -m ledger_recon.cli run --data-dir examples/dummy_data --output-dir output

Figures are for `2026-01-05`. The full workbook (`recon_2026-01-05.xlsx`) has seven sheets; the
tables here are excerpts from them.

## Summary sheet

| metric | value |
|---|---|
| total_rows | 70 |
| recon | 62 |
| unrecon | 8 |
| bank_only | 5 |
| ledger_only | 3 |
| ambiguous_rows | 4 |
| wrong_routing | 1 |
| weak_matches | 2 |
| rejected_rows | 3 |
| statement_placeholder_rows | 0 |

## Unreconciled rows (`Match?` = `Unrecon`)

Each row carries a `case` that says *why* it is open. The bank-only row for the 250000 group
shows pairing ambiguity (4 bank rows against 3 ledger rows); the 150000/155000 pair is an amount
mismatch, and its bank-side row received the username from same-day reference history.

| reference | amount | bank_txn_id | ledger_id | ledger_username | case |
|---|---|---|---|---|---|
| 8800183843883356 | 77000 | B0105-0063 |  |  | WAITING_LEDGER |
| 8800339765173374 | 250000 | B0105-0062 |  | merchant_3 | BANK 4x vs LEDGER 3x \| username auto-filled from same-day reference history |
| 8800375525167717 | 64000 |  | L0105-0063 | merchant_2 | LEDGER SUCCESS WITHOUT BANK CREDIT (ledger 1x, bank 0x) - MANUAL REVIEW |
| 8800378488948999 | 78000 | B0105-0064 |  |  | WAITING_LEDGER |
| 8800394101443622 | 91000 | B0105-0071 |  |  | WAITING_LEDGER |
| 8800410644026623 | 65000 |  | L0105-0064 | merchant_2 | LEDGER SUCCESS WITHOUT BANK CREDIT (ledger 1x, bank 0x) - MANUAL REVIEW |
| 8800439498116277 | 150000 | B0105-0065 |  | merchant_5 | WAITING_LEDGER \| username auto-filled from same-day reference history |
| 8800439498116277 | 155000 |  | L0105-0062 | merchant_5 | LEDGER SUCCESS WITHOUT BANK CREDIT (ledger 1x, bank 0x) - MANUAL REVIEW |

## Ambiguous group (every row in it is flagged, including the `Recon` ones)

| reference | amount | bank_txn_id | ledger_id | Match? |
|---|---|---|---|---|
| 8800339765173374 | 250000 | B0105-0059 | L0105-0059 | Recon |
| 8800339765173374 | 250000 | B0105-0060 | L0105-0060 | Recon |
| 8800339765173374 | 250000 | B0105-0061 | L0105-0061 | Recon |
| 8800339765173374 | 250000 | B0105-0062 |  | Unrecon |

## Weak time matches (matched, but 25 minutes apart)

| reference | amount | Time_Gap_Minutes | Match_Quality |
|---|---|---|---|
| 8800230553940031 | 33000 | 25 | Weak - MANUAL CHECK REQUIRED |
| 8800589548681103 | 34000 | 25 | Weak - MANUAL CHECK REQUIRED |

## Mis-routed payment

| reference | bank_account_no | Routing_Check |
|---|---|---|
| 8800175501038782 | 0100000003 | WRONG ROUTING (expected 0100000001) |

## Rejected rows (never silently coerced)

| source | reject_reason |
|---|---|
| statement | invalid amount |
| ledger | invalid created_at |
| ledger | invalid created_at |

## Tracker after the full run

Open items live in month sheets named `YYYY-MM`; a late-arriving ledger row closed the
cross-midnight payment (`Resolved`), and `Consumed_Ledger_IDs` stops one ledger row from
resolving two bank rows.

| sheet | rows |
|---|---|
| 2026-01 | 14 |
| Resolved | 2 |
| Consumed_Ledger_IDs | 125 |


## Timezone-aware output (v0.2.0+)

Two columns are added to the **Line Per Line** sheet:

| column | example | meaning |
|---|---|---|
| `bank_posted_at_gmt7` | `2026-01-05 10:36:00 +0700` | Bank timestamp, explicit WIB offset |
| `ledger_created_at_gmt7` | `2026-01-05 10:36:28 +0700` | Ledger timestamp, explicit WIB offset |

Both are **strings**, not datetimes. This is deliberate: Excel cannot store a
timezone offset on a datetime cell, so a real datetime column would silently
drop the `+0700` on write. A string survives the round-trip.

NaT values become an empty string, never `NaN`, so the sheet stays clean.

### Metabase verification columns

When a `metabase_YYYY-MM-DD.csv` file is present for a day, an additional
column `Metabase_Status` appears on the **Line Per Line** sheet. It is only
populated for `Unrecon` rows:

| value | meaning |
|---|---|
| `Unrecon - pending GDS` | Present and final in Metabase; GDS export is behind |
| `Unrecon - confirmed missing` | Absent or not final in Metabase; real break |
| `Unrecon - no reference` | No reference extracted from the description |
| *empty* | Row is `Recon`; verification not applicable |

When no Metabase file exists for the day, the column is absent and the report
is identical to the pre-verification pipeline.


## Metabase verification in the sample data

The sample data ships with two Metabase exports (`metabase_2026-01-05.csv`
and `metabase_2026-01-06.csv`). Timestamps inside them are UTC, seven hours
behind the WIB timestamps in the bank statements -- deliberately, so that
the timezone normalisation is exercised end-to-end.

Running the pipeline over the sample data classifies the unreconciled rows
like this:

| day | pending GDS | confirmed missing | no reference |
|---|---|---|---|
| 2026-01-05 | 3 | 5 | 0 |
| 2026-01-06 | 2 | 6 | 0 |

What each number means on this sample:

- **pending GDS** (3 on day 1): a bank-only row (77000), the bank-side of
  an amount mismatch (150000), and the bank-side of a cross-midnight
  payment (91000). All three exist and are final in Metabase, so they are
  waiting for the GDS export, not real breaks.
- **confirmed missing** (5 on day 1): one bank-only row whose Metabase
  status is PENDING rather than FINAL (78000), the fourth row of the
  ambiguous 250000 group, and three ledger-only rows that have no bank
  counterpart to join to.
- **no reference** (0): every unreconciled row on these days had an
  extractable reference, so no row was skipped at the first classification
  step.

These numbers are asserted indirectly through `tests/test_metabase.py`,
which checks the classification rules on small hand-built frames rather
than on the full sample data.
