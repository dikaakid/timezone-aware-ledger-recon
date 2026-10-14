"""Daily batch pipeline: discover files, skip finished dates, reconcile, report, track."""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .config import ReconConfig
from .core import build_line_per_line, statement_looks_incomplete, summarize
from .loaders import filter_window, load_ledger, load_statement
from .metabase import (
    UNRECON_CONFIRMED_MISSING,
    UNRECON_LEDGER_ONLY_UNCONFIRMED,
    UNRECON_NO_REF,
    UNRECON_PENDING_BANK_POSTING,
    UNRECON_PENDING_GDS,
    UNRECON_PENDING_UPSTREAM_BANK,
    UNRECON_PENDING_UPSTREAM_LEDGER,
    load_metabase,
    verify_unrecon,
)
from .report import account_pivot, summary_frame, write_report
from .tracker import TRACKER_NAME, ensure_tracker_valid, recheck_tracker, record_consumed_ids, update_tracker

STATEMENT_RE = re.compile(r"^statement_(\d{4}-\d{2}-\d{2})\.csv$", re.IGNORECASE)
LEDGER_RE = re.compile(r"^ledger_(\d{4}-\d{2}-\d{2})(?:_(\d{4}-\d{2}-\d{2}))?\.csv$", re.IGNORECASE)
METABASE_RE = re.compile(r"^metabase_(\d{4}-\d{2}-\d{2})\.csv$", re.IGNORECASE)
OUTPUT_TEMPLATE = "recon_{d}.xlsx"


def index_statements(folder: Path) -> dict:
    found = {}
    for fp in Path(folder).glob("*.csv"):
        m = STATEMENT_RE.match(fp.name)
        if m:
            found[date.fromisoformat(m.group(1))] = fp
    return found


def index_ledgers(folder: Path) -> list:
    """(start, end, path) for daily files and for bulk range files."""
    entries = []
    for fp in Path(folder).glob("*.csv"):
        m = LEDGER_RE.match(fp.name)
        if m:
            start = date.fromisoformat(m.group(1))
            end = date.fromisoformat(m.group(2)) if m.group(2) else start
            entries.append((start, end, fp))
    return entries


def index_metabase(folder: Path) -> dict:
    """{date: path} for metabase_YYYY-MM-DD.csv files."""
    found = {}
    for fp in Path(folder).glob("*.csv"):
        m = METABASE_RE.match(fp.name)
        if m:
            found[date.fromisoformat(m.group(1))] = fp
    return found


def _attach_metabase_status(lpl: pd.DataFrame, mb: pd.DataFrame, cfg: ReconConfig) -> pd.DataFrame:
    """Merge Metabase verification status onto unreconciled rows.

    Recon rows keep Metabase_Status = None. Unrecon rows are checked
    against Metabase, except amount-mismatch rows: those have already
    been classified as a different kind of break (a bank row and a ledger
    row that share a reference but disagree on amount). Running the
    Metabase lookup on them would only reinforce the wrong label: the
    verification layer confirms a single (reference, amount, occurrence)
    key against Metabase, it does not arbitrate between two sides of a
    disagreement.
    """
    lpl = lpl.copy()
    unrecon_mask = lpl["Match?"] == "Unrecon"
    if "Amount_Mismatch" in lpl.columns:
        unrecon_mask = unrecon_mask & ~lpl["Amount_Mismatch"].fillna(False)
    if not unrecon_mask.any():
        lpl["Metabase_Status"] = None
        return lpl

    verified = verify_unrecon(lpl.loc[unrecon_mask].copy(), mb, cfg)
    merged = lpl.merge(
        verified[["reference", "amount_minor", "occurrence", "Metabase_Status"]],
        on=["reference", "amount_minor", "occurrence"],
        how="left",
    )
    return merged


def ledgers_for(index: list, start: date, end: date) -> list:
    seen, out = set(), []
    for s, e, fp in index:
        if s <= end and e >= start and fp not in seen:
            seen.add(fp)
            out.append(fp)
    return out


def reconcile_day(
    statement_src,
    ledger_srcs: list,
    d: date,
    cfg: ReconConfig,
    force: bool = False,
    log=print,
    metabase_src=None,
):
    """Reconcile one calendar day. Returns a dict, or None when the day is skipped."""
    w0, w1 = d - timedelta(days=cfg.ledger_window_days), d + timedelta(days=cfg.ledger_window_days)

    statement, st_rej, st_meta, extractor = load_statement(statement_src, cfg)
    frames, rejects = [], [st_rej.assign(source="statement")]
    for src in ledger_srcs:
        led, led_rej, _ = load_ledger(src, cfg)
        frames.append(led)
        rejects.append(led_rej.assign(source="ledger"))
    ledger_all = (
        pd.concat(frames, ignore_index=True).drop_duplicates("ledger_id")
        if frames
        else load_ledger(
            pd.DataFrame(columns=["ledger_id", "reference", "amount", "created_at", "status"]), cfg
        )[0]
    )
    ledger, dropped = filter_window(ledger_all, w0, w1)
    if dropped:
        log(f"  [WARNING] {dropped} ledger rows outside window {w0}..{w1} were dropped")

    rejected = pd.concat(rejects, ignore_index=True)
    if len(rejected):
        log(f"  [WARNING] {len(rejected)} rows rejected (see 'Rejected Rows' sheet)")

    n_cr_ref = int(((statement["direction"] == "CR") & statement["reference"].notna()).sum())
    n_ledger_day = int((ledger["ledger_date"] == d).sum())
    if n_cr_ref == 0 and n_ledger_day == 0:
        log(f"  [SKIPPED] {d}: no referenced credits and no ledger rows on this date")
        return None

    suspicious, reasons = statement_looks_incomplete(statement, ledger, d, st_meta, cfg)
    if suspicious and not force:
        log(f"  [SKIPPED - STATEMENT LOOKS INCOMPLETE] {d}")
        for r in reasons:
            log(f"    - {r}")
        log(f"    Re-download the statement, or override with: --force {d.isoformat()}")
        return None

    lpl = build_line_per_line(statement, ledger, d, cfg)
    if metabase_src is not None:
        mb = load_metabase(metabase_src, cfg)
        lpl = _attach_metabase_status(lpl, mb, cfg)
    anomalies = extractor.anomaly_summary()
    if anomalies:
        log(
            f"  [WARNING] {len(anomalies)} reference-like digit runs have an UNKNOWN prefix "
            f"(treated as non-reference):"
        )
        for digits, info in anomalies.items():
            log(f"    - {digits} ({info['channel']}, {info['count']}x)")

    summary = summarize(lpl, rejected=len(rejected))
    ledger_today = ledger[ledger["ledger_date"] == d].reset_index(drop=True)
    sheets = {
        "Statement Raw": statement.drop(columns=["amount_minor", "posted_date"]),
        "Ledger Raw": ledger_today.drop(columns=["amount_minor", "ledger_date"]),
        "Line Per Line": lpl,
        "Non-Reference Rows": statement[statement["reference"].isna()]
        .drop(columns=["amount_minor", "posted_date"])
        .reset_index(drop=True),
        "Rejected Rows": rejected,
        "Summary": summary_frame(summary, {"statement_placeholder_rows": st_meta["placeholder_rows"]}),
        "Pivot per Account": account_pivot(statement),
    }
    unrecon = lpl[lpl["Match?"] == "Unrecon"].copy()
    consumed = lpl.loc[lpl["Match?"] == "Recon", "ledger_id"].dropna().astype(str).tolist()
    return {
        "lpl": lpl,
        "summary": summary,
        "sheets": sheets,
        "unrecon": unrecon,
        "consumed_ids": consumed,
        "ledger_pool": ledger_all,
    }


def run_batch(
    data_dir: Path, output_dir: Path, cfg: ReconConfig, force=(), recheck: bool = True, log=print
) -> dict:
    data_dir, output_dir = Path(data_dir), Path(output_dir)
    tracker = output_dir / TRACKER_NAME
    ensure_tracker_valid(tracker)

    statements = index_statements(data_dir)
    ledgers = index_ledgers(data_dir)
    metabase_index = index_metabase(data_dir)
    log(
        f"Found {len(statements)} statements, {len(ledgers)} ledger files, "
        f"{len(metabase_index)} metabase files."
    )

    def done(d):
        return (output_dir / OUTPUT_TEMPLATE.format(d=d.isoformat())).exists()

    todo = [d for d in sorted(statements) if not done(d)]
    log(
        f"Skipping {len(statements) - len(todo)} already processed; "
        f"processing {[d.isoformat() for d in todo]}"
    )

    force = {date.fromisoformat(x) if isinstance(x, str) else x for x in force}
    results, unrecon_parts, consumed = {}, [], []
    for d in todo:
        log(f"\n=== Processing {d.isoformat()} ===")
        w0, w1 = d - timedelta(days=cfg.ledger_window_days), d + timedelta(days=cfg.ledger_window_days)
        res = reconcile_day(
            statements[d],
            ledgers_for(ledgers, w0, w1),
            d,
            cfg,
            d in force,
            log,
            metabase_src=metabase_index.get(d),
        )
        if res is None:
            continue
        out_path = output_dir / OUTPUT_TEMPLATE.format(d=d.isoformat())
        write_report(res["sheets"], out_path)
        s = res["summary"]
        log(
            f"  Line Per Line: {s['total_rows']} rows | Recon {s['recon']} | Unrecon {s['unrecon']} "
            f"| ambiguous {s['ambiguous_rows']} | wrong routing {s['wrong_routing']} "
            f"| weak matches {s['weak_matches']}"
        )
        lpl_cols = res["lpl"].columns
        if "Metabase_Status" in lpl_cols:
            counts = res["lpl"]["Metabase_Status"].value_counts()
            log(
                f"  Unrecon (bank side): "
                f"pending_gds {int(counts.get(UNRECON_PENDING_GDS, 0))} | "
                f"pending_upstream {int(counts.get(UNRECON_PENDING_UPSTREAM_BANK, 0))} | "
                f"confirmed_missing {int(counts.get(UNRECON_CONFIRMED_MISSING, 0))}"
            )
            log(
                f"  Unrecon (ledger side): "
                f"pending_bank_posting {int(counts.get(UNRECON_PENDING_BANK_POSTING, 0))} | "
                f"pending_upstream {int(counts.get(UNRECON_PENDING_UPSTREAM_LEDGER, 0))} | "
                f"ledger_only_unconfirmed {int(counts.get(UNRECON_LEDGER_ONLY_UNCONFIRMED, 0))}"
            )
            log(f"  Unrecon (no reference): {int(counts.get(UNRECON_NO_REF, 0))}")
        log(f"  -> Saved: {out_path}")
        results[d] = s
        if not res["unrecon"].empty:
            unrecon_parts.append(res["unrecon"])
        consumed.extend(res["consumed_ids"])

    if consumed:
        record_consumed_ids(consumed, tracker, cfg)
    if unrecon_parts:
        log("\n=== Updating unreconciled tracker ===")
        update_tracker(pd.concat(unrecon_parts, ignore_index=True), tracker, cfg)

    recheck_result = None
    if recheck and tracker.exists():
        pool_frames = [load_ledger(fp, cfg)[0] for _, _, fp in ledgers]
        if pool_frames:
            pool = pd.concat(pool_frames, ignore_index=True).drop_duplicates("ledger_id")
            recheck_result = recheck_tracker(tracker, pool, cfg)
            log(
                f"\n=== Recheck === resolved rows moved: {recheck_result['resolved']} | "
                f"still pending: {recheck_result['still_pending']}"
            )
    log("\nDone.")
    return {"days": results, "recheck": recheck_result}
