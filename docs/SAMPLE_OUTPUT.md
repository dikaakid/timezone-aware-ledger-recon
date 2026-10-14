# Sample output

Everything below is produced from the **synthetic** data in `examples/dummy_data/` by:

    python -m ledger_recon.cli run --data-dir examples/dummy_data --output-dir output

Figures are for `2026-01-05`. The full workbook (`recon_2026-01-05.xlsx`) has seven sheets; the
tables here are excerpts from them.

## Summary sheet

| metric | value |
|---|---|
| total_rows | 71 |
| recon | 62 |
| unrecon | 9 |
| bank_only | 5 |
| ledger_only | 4 |
| ambiguous_rows | 4 |
| wrong_routing | 1 |
| weak_matches | 2 |
| amount_mismatch_rows | 2 |
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
| 8800599988877777 | 200000 |  | L0105-0069 | merchant_pass0 | LEDGER SUCCESS WITHOUT BANK CREDIT (ledger 1x, bank 0x) - MANUAL REVIEW |

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
| Resolved | 4 |
| Consumed_Ledger_IDs | 126 |


## Amount mismatch detection

A bank row and a ledger row that share a reference but disagree on
amount are kept in separate groups by the composite key
`(reference, amount_minor, occurrence)`. Without help, they appear as
two independent breaks: the bank row as `WAITING_LEDGER`, the ledger
row as `LEDGER SUCCESS WITHOUT BANK CREDIT`. An operator following the
first label would wait for a ledger row that will never arrive -- the
row exists, but with a different amount.

`detect_amount_mismatch()` recognises the pattern when, within one
reference, there is exactly one Unrecon bank-only row and exactly one
Unrecon ledger-only row. Both rows are then labelled with a distinct
`case`:

    AMOUNT MISMATCH (bank 150,000.00 vs ledger 155,000.00, delta +5,000.00)

and three new columns are populated:

| column | type | value |
|---|---|---|
| `Amount_Mismatch` | bool | `True` on both rows of a detected pair |
| `Amount_Delta` | Float64 (major units) | signed `ledger - bank` difference, same scale as `amount` |
| `Amount_Delta_Minor` | Int64 (minor units) | signed `ledger - bank` difference, exact |

The delta is positive when the ledger claims more than the bank
received, negative in the opposite case. The `case` label shows the
same number with an explicit sign so a finance reader can see the
direction at a glance.

Example from the sample data on 2026-01-05:

| reference | amount | bank_txn_id | ledger_id | Amount_Delta | Amount_Delta_Minor | case |
|---|---|---|---|---|---|---|
| 8800439498116277 | 150000 | B0105-0065 | | 5000.00 | 500000 | AMOUNT MISMATCH (bank 150,000.00 vs ledger 155,000.00, delta +5,000.00) \| username auto-filled from same-day reference history |
| 8800439498116277 | 155000 | | L0105-0062 | 5000.00 | 500000 | AMOUNT MISMATCH (bank 150,000.00 vs ledger 155,000.00, delta +5,000.00) |

The `summary` sheet reports `amount_mismatch_rows = 2` (both rows of
the one pair found on this day).

Rows flagged `Amount_Mismatch = True` are **not** sent to Metabase during
verification. The verification layer is built to confirm a single
`(reference, amount, occurrence)` key against Metabase, not to arbitrate
between two sides of a disagreement. Metabase will agree with whichever
side matches its data, but which side that is differs per transaction;
the tool leaves the row unverified rather than guess.

## Timezone-aware output

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
| `Unrecon - pending GDS` | Bank-only, present and FINAL in Metabase; wait for GDS |
| `Unrecon - pending upstream status (bank)` | Bank-only, present but not FINAL in Metabase |
| `Unrecon - confirmed missing` | Bank-only, absent from Metabase; real break |
| `Unrecon - pending bank posting` | Ledger-only, present and FINAL in Metabase; wait for bank |
| `Unrecon - pending upstream status (ledger)` | Ledger-only, present but not FINAL in Metabase |
| `Unrecon - ledger only, no confirmation` | Ledger-only, absent from Metabase; investigate ledger |
| `Unrecon - no reference` | No reference extracted from the description |
| *empty* | Row is `Recon`, or `Amount_Mismatch = True`; verification not applicable |

When no Metabase file exists for the day, the column is absent and the report
is identical to the pre-verification pipeline.


## Metabase verification in the sample data

The sample data ships with two Metabase exports (`metabase_2026-01-05.csv`
and `metabase_2026-01-06.csv`). Timestamps inside them are UTC, seven hours
behind the WIB timestamps in the bank statements -- deliberately, so that
the timezone normalisation is exercised end-to-end.

Running the pipeline over the sample data classifies the unreconciled rows
like this:

| day | bank side (pg / up / miss) | ledger side (pbp / up / unconf) | no reference |
|---|---|---|---|
| 2026-01-05 | 2 / 1 / 1 | 0 / 0 / 3 | 0 |
| 2026-01-06 | 1 / 1 / 1 | 0 / 0 / 3 | 0 |

Abbreviations:

- **pg** = `pending GDS`: present and FINAL in Metabase; wait for the
  bank statement.
- **up** = `pending upstream status (bank/ledger)`: present in Metabase
  but not FINAL yet; wait for Metabase to finalize.
- **miss** = `confirmed missing`: absent from Metabase; a real break.
- **pbp** = `pending bank posting`: present and FINAL in Metabase but the
  bank statement has not posted it yet.
- **unconf** = `ledger only, no confirmation`: ledger recorded it, but
  Metabase has no record either. Investigate the ledger entry.

Rows flagged `Amount_Mismatch = True` are not sent to Metabase (see
"Amount mismatch detection" above); their `Metabase_Status` stays empty
and they are not counted in this table. On 2026-01-05 that is 2 rows
(the bank and ledger sides of the 150000 vs 155000 pair).

What each number means on this sample:

- **2026-01-05, bank side**: 2 waiting for GDS (77000 and the
  cross-midnight 91000), 1 waiting for Metabase to finalize (78000,
  status PENDING), 1 real break (the fourth row of the ambiguous 250000
  group has no Metabase record).
- **2026-01-05, ledger side**: 2 ledger-only rows (64000, 65000) with no
  Metabase confirmation at all -- the ledger recorded them but nothing
  else did.
- **no reference** (0): every unreconciled row on these days had an
  extractable reference, so no row was skipped at the first classification
  step.

These numbers are asserted indirectly through `tests/test_metabase.py`,
which checks the classification rules on small hand-built frames rather
than on the full sample data.
