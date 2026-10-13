# Timezone-Aware Ledger Reconciliation

[![CI](https://github.com/dikaakid/timezone-aware-ledger-recon/actions/workflows/ci.yml/badge.svg)](https://github.com/dikaakid/timezone-aware-ledger-recon/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.12%20%7C%203.13-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Code style: ruff](https://img.shields.io/badge/lint-ruff-261230)](https://docs.astral.sh/ruff/)

Reconcile a **bank statement**, an **internal transaction ledger**, and a **verification system**
that describe the same events **in different timezones**, and get an Excel report where every
timestamp is expressed in WIB (GMT+7).

## The problem

Three systems record the same payments. They were built independently, so there is no shared
unique transaction ID to join on — the same situation the companion
[ledger-reconciliation-tool](https://github.com/dikaakid/ledger-reconciliation-tool) solves.

What makes this case different is that the three systems also **disagree on time**:

| Source | Timezone | Role |
|---|---|---|
| Bank statement | WIB (GMT+7) | External truth: what actually hit the account |
| Internal ledger (GDS) | WIB (GMT+7) | Internal source of record, ready-to-serve |
| Verification system (Metabase) | UTC (GMT+0) | Secondary check on rows that did not match |

A naive `pd.to_datetime` treats all three the same. The failure mode is **silent**: a bank
transaction at `2026-10-12 00:30 WIB` is compared against a Metabase row from
`2026-10-11 17:30 UTC` — the *previous* UTC day — and the reconciliation either misses it,
or reports a 7-hour gap that looks like a weak match. Either way the numbers are wrong and
nobody notices.

## What it does

| Capability | How |
|---|---|
| **Normalises timezones at the boundary** | Bank & GDS tagged as `Asia/Jakarta`, Metabase as `UTC` then converted to `Asia/Jakarta`. The matching engine itself stays timezone-agnostic. |
| Matches repeated identical payments one-to-one | Composite key `(reference, amount, occurrence index)`, computed **after** timezone normalisation |
| Extracts references from free-text bank descriptions | Exact-length, prefix-validated patterns (never a greedy range that swallows glued digits) |
| Warns when pairing cannot be trusted | If a reference+amount group has a different row count on each side, the **whole group** is flagged, including rows marked `Recon` |
| Scores every match | `Strong` (<= 1 min), `Moderate` (<= 10 min), `Weak` (> 10 min) by time gap, all in WIB |
| Verifies unmatched rows against a second system | `Unrecon` rows are re-checked against Metabase using `(reference, amount, occurrence, day)` — never by reference alone |
| Distinguishes "pending" from "missing" | Output labels `Unrecon - pending GDS` vs `Unrecon - confirmed missing` |
| Refuses bad input quietly passing as good | Unparseable timestamps/amounts are **rejected and reported**, never coerced; leading zeros survive; amounts compared as integer minor units |
| Keeps a cumulative tracker of open items | Month sheets (`YYYY-MM`), idempotent re-runs, validated + backed-up + atomic writes, bounded recheck |

## Quickstart

    pip install -r requirements.txt
    python -m ledger_recon.cli run --data-dir examples/dummy_data --output-dir output

(or `pip install -e .` and use the `timezone-recon` command).

Expected output on the sample data (verified by `tests/test_pipeline.py`):

    Found 3 statements, 2 ledger files, 2 metabase files.

    === Processing 2026-01-05 ===
      Line Per Line: 70 rows | Recon 62 | Unrecon 8 | ambiguous 4 | wrong routing 1 | weak matches 2
      Unrecon verified against Metabase: pending_gds 3 | confirmed_missing 5 | no_reference 0
    === Processing 2026-01-06 ===
      Line Per Line: 70 rows | Recon 62 | Unrecon 8 | ambiguous 4 | wrong routing 1 | weak matches 2
      Unrecon verified against Metabase: pending_gds 2 | confirmed_missing 6 | no_reference 0
    === Processing 2026-01-07 ===
      [SKIPPED - STATEMENT LOOKS INCOMPLETE] 2026-01-07
    === Recheck === resolved rows moved: 2 | still pending: 14

See [docs/SAMPLE_OUTPUT.md](docs/SAMPLE_OUTPUT.md) for real excerpts of the report.

One day only:

    python -m ledger_recon.cli pair \
        examples/dummy_data/statement_2026-10-12.csv \
        examples/dummy_data/ledger_2026-10-12.csv \
        examples/dummy_data/metabase_2026-10-12.csv \
        --date 2026-10-12 -o report.xlsx

## Input format

Files are discovered by name in `--data-dir`:

* `statement_YYYY-MM-DD.csv` — bank statement, WIB
* `ledger_YYYY-MM-DD.csv` or bulk range `ledger_YYYY-MM-DD_YYYY-MM-DD.csv` — internal ledger, WIB
* `metabase_YYYY-MM-DD.csv` — verification system, **UTC**

| Statement column | Meaning |
|---|---|
| `txn_id` | Unique id within the statement |
| `account_no` | Account the money landed in (kept as text) |
| `posted_at` | ISO-8601 timestamp in WIB |
| `description` | Free text the reference is extracted from |
| `direction` | `CR` or `DB` (only credits are matched) |
| `amount` | Numeric |

| Ledger column | Meaning |
|---|---|
| `ledger_id` | Unique id within the ledger |
| `reference` | Payment reference |
| `amount` | Numeric |
| `created_at` | ISO-8601 timestamp in WIB |
| `status` | Only `SUCCESS` rows (configurable) are reconciled |
| `username` | Optional; used to pre-fill the owner of unmatched bank rows |

| Metabase column | Meaning |
|---|---|
| `trx_id` | Unique id in the verification system |
| `reference` | Payment reference |
| `amount` | Numeric |
| `created_at` | ISO-8601 timestamp in **UTC** — converted to WIB on load |
| `status` | Only rows with a final status are used for verification |

If your files use other column names, map them in a JSON config (`statement_columns`,
`ledger_columns`, `metabase_columns`) — see below.

## Timezone model

The rule is simple and explicit:

| Source | Attached timezone | Converted on load? |
|---|---|---|
| `statement` | `Asia/Jakarta` (WIB) | No — already WIB |
| `ledger` | `Asia/Jakarta` (WIB) | No — already WIB |
| `metabase` | `UTC` | Yes — converted to `Asia/Jakarta` |

Two failure modes this prevents:

1. **Double conversion.** GDS is *already* in WIB. Treating it as UTC and converting would shift
   every row by 7 hours and produce a report where nothing matches.
2. **Silent day boundary drift.** A transaction at `00:30 WIB` belongs to *that* calendar day.
   If Metabase reports it as `17:30 UTC` on the *previous* day, the daily bucket must still be
   the WIB day. Normalisation happens before any bucket is chosen.

All output timestamps carry a `_gmt7` suffix so the reader never has to guess the timezone.
See [docs/TIMEZONE.md](docs/TIMEZONE.md) for the full design rationale.

## Verification layer

After the primary reconciliation (bank vs GDS), every `Unrecon` row is checked against Metabase:

* Join key is `(reference, amount_minor, occurrence)` inside the same WIB day — **never by
  reference alone**, because a reference can be reused many times in a day.
* Only rows with a final status in Metabase count as verified.
* Output labels distinguish three cases:
  * `Unrecon - pending GDS` — transaction exists and is final in Metabase, but has not yet
    appeared in the GDS export. Data pipeline lag, not a real break.
  * `Unrecon - confirmed missing` — transaction is absent or not final in Metabase. A real
    break that needs investigation.
  * `Unrecon - no reference` — no reference could be extracted from the bank description.

This keeps the finance team from chasing false breaks caused by upstream export timing.

## Configuration

Everything provider-specific lives in `ledger_recon/config.py` and can be overridden with
`--config my_config.json`:

    {
      "candidate_lengths": [18, 17, 16],
      "prefix_to_account": {"12345": "0011223344"},
      "statement_columns": {"trx_id": "txn_id"},
      "ledger_columns": {"trx_id": "ledger_id", "va": "reference"},
      "metabase_columns": {"id": "trx_id", "va": "reference"},
      "source_timezones": {
        "statement": "Asia/Jakarta",
        "ledger": "Asia/Jakarta",
        "metabase": "UTC"
      }
    }

The `source_timezones` map is the single place where timezone policy is decided. Changing it
changes behaviour everywhere, with no hidden conversions.

## Output

`recon_YYYY-MM-DD.xlsx` per day with sheets: **Statement Raw**, **Ledger Raw**, **Metabase Raw**,
**Line Per Line** (the result; filter `Match?`, `case`, `Pairing_Ambiguous`, `Routing_Check`,
`Metabase_Status`), **Non-Reference Rows**, **Rejected Rows**, **Summary**, **Pivot per Account**.
Plus one cumulative `unreconciled_tracker.xlsx` (open items by month, `Resolved`, and a
consumed-ID registry).

Every timestamp column is emitted twice: the raw value and the `_gmt7` normalised value.

## Known limitations

* **Same-day window by design.** A payment that crosses midnight appears as unreconciled on its
  statement day and is closed by the tracker recheck (up to `recheck_max_days` later).
* **Pairing is by chronological order, not by optimal time assignment.** When group counts differ,
  the pairing may be off by one position; that is exactly why the whole group is flagged.
* **Exact amount match.** Fee- or FX-adjusted amounts need a tolerance-based approach, which this
  tool does not implement.
* **One currency per run.** Split files per currency.
* **Metabase is a verification layer, not a primary source.** It confirms or rejects `Unrecon`
  rows. It does not introduce new matched rows.

## Relationship to ledger-reconciliation-tool

This project shares the same matching philosophy as
[ledger-reconciliation-tool](https://github.com/dikaakid/ledger-reconciliation-tool):

* composite key `(reference, amount, occurrence)`,
* whole-group ambiguity flag,
* reject-and-report for bad input,
* idempotent tracker with atomic writes.

What this project adds is explicit, per-source timezone normalisation and a secondary
verification layer. Those two features only matter when the sources disagree on time or when
a second system is available for confirmation.

**If your sources all report in the same timezone**, `ledger-reconciliation-tool` is simpler,
has fewer moving parts, and is the recommended choice. **If your sources span timezones** or
you need a verification layer, use this one. The two tools are companions, not versions of
each other.

## Project structure

    ledger_recon/        engine: config, tz, reference, loaders, core, metabase, tracker, report, pipeline, cli
    tests/               pytest suite (timezone boundary, metabase verification, matching, tracker, end-to-end)
    examples/            synthetic sample data (WIB + UTC) + generator
    docs/                ARCHITECTURE.md (design rationale), TIMEZONE.md (timezone decisions),
                         SAMPLE_OUTPUT.md (real output excerpts)
    .github/             CI workflow, issue and pull-request templates

## Development

    pip install -e ".[dev]"
    ruff format . && ruff check .
    pytest

The suite covers timezone boundaries, Metabase verification, extraction, loading rules,
matching, tracker safety and an end-to-end run on the sample data. Boundary tests explicitly
prove that a `00:30 WIB` row and a `17:30 UTC` row from the previous day land in the same
WIB daily bucket. CI runs tests on Python 3.10, 3.12 and 3.13 on Linux and Windows, plus
lint and a package build.

## License

MIT — see [LICENSE](LICENSE).