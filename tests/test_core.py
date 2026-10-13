from datetime import date

import pandas as pd
from conftest import DAY, L_COLS, REF2, S_COLS, bank, led

from ledger_recon.core import statement_looks_incomplete, summarize
from ledger_recon.loaders import filter_window, load_ledger, load_statement

T = "2026-01-05 10:00:00"


def test_single_pair_is_recon_and_strong(run):
    r = run([bank("B1", T)], [led("L1", "2026-01-05 10:00:20")])
    assert r["Match?"].tolist() == ["Recon"] and r.loc[0, "Match_Quality"] == "Strong"
    assert r.loc[0, "Routing_Check"] == "OK" and pd.isna(r.loc[0, "Pairing_Ambiguous"])


def test_identical_rows_pair_in_chronological_order(run):
    r = run(
        [bank("B1", "2026-01-05 09:00:00"), bank("B2", "2026-01-05 15:00:00")],
        [led("L1", "2026-01-05 09:00:05"), led("L2", "2026-01-05 15:00:05")],
    )
    pairs = dict(zip(r["bank_txn_id"], r["ledger_id"], strict=True))
    assert pairs == {"B1": "L1", "B2": "L2"}


def test_bank_only_and_ledger_only_labels(run):
    r = run([bank("B1", T)], [led("L1", T, ref=REF2)])
    cases = dict(zip(r["bank_txn_id"].fillna("-"), r["case"], strict=True))
    assert cases["B1"] == "WAITING_LEDGER"
    assert cases["-"].startswith("LEDGER SUCCESS WITHOUT BANK CREDIT")


def test_amount_mismatch_creates_two_unrecon_rows_and_fills_username(run):
    r = run([bank("B1", T, amount=150000)], [led("L1", T, amount=155000, user="merchant_9")])
    assert (r["Match?"] == "Unrecon").all() and len(r) == 2
    bank_row = r[r["bank_txn_id"] == "B1"].iloc[0]
    assert bank_row["ledger_username"] == "merchant_9" and "auto-filled" in bank_row["case"]


def test_count_mismatch_flags_the_whole_group_including_recon_rows(run):
    st = [bank(f"B{i}", f"2026-01-05 10:0{i}:00", amount=250000) for i in range(4)]
    lg = [led(f"L{i}", f"2026-01-05 10:0{i}:10", amount=250000) for i in range(3)]
    r = run(st, lg)
    assert r["Pairing_Ambiguous"].notna().all() and len(r) == 4
    assert (r["Match?"] == "Recon").sum() == 3  # recon rows are flagged too


def test_one_sided_group_is_not_ambiguous(run):
    r = run([bank(f"B{i}", f"2026-01-05 10:0{i}:00") for i in range(3)], [])
    assert r["Pairing_Ambiguous"].isna().all()
    assert set(r["case"]) == {"BANK 3x, NOT FOUND IN LEDGER"}


def test_weak_time_match_is_labelled_and_counted(run):
    r = run([bank("B1", "2026-01-05 10:00:00")], [led("L1", "2026-01-05 10:25:00")])
    assert r.loc[0, "Match_Quality"].startswith("Weak")
    assert summarize(r)["weak_matches"] == 1


def test_float_noise_does_not_break_matching(run):
    r = run([bank("B1", T, amount=0.3)], [led("L1", T, amount=0.1 + 0.2)])
    assert r["Match?"].tolist() == ["Recon"]


def test_debit_rows_are_never_matched(run):
    r = run([bank("B1", T, direction="DB")], [led("L1", T)])
    assert "B1" not in r["bank_txn_id"].dropna().tolist() and r["Match?"].tolist() == ["Unrecon"]


def test_wrong_routing_is_flagged(run):
    r = run([bank("B1", T, account="0100000003")], [led("L1", T)])
    assert r.loc[0, "Routing_Check"] == "WRONG ROUTING (expected 0100000001)"


def test_routing_rule_not_applied_before_effective_date(run):
    r = run(
        [bank("B1", "2025-12-01 10:00:00", account="0100000003")],
        [led("L1", "2025-12-01 10:00:00")],
        d=date(2025, 12, 1),
    )
    assert pd.isna(r.loc[0, "Routing_Check"])


def test_username_not_filled_when_reference_has_two_usernames(run):
    r = run([bank("B1", T, amount=5)], [led("L1", T, amount=6, user="a"), led("L2", T, amount=7, user="b")])
    row = r[r["bank_txn_id"] == "B1"].iloc[0]
    assert pd.isna(row["ledger_username"]) and "NOT auto-filled" in row["case"]


def test_next_day_ledger_rows_are_out_of_scope(cfg):
    st, *_ = load_statement(pd.DataFrame([bank("B1", T)], columns=S_COLS), cfg)
    lg, *_ = load_ledger(pd.DataFrame([led("L1", "2026-01-06 00:00:20")], columns=L_COLS), cfg)
    kept, dropped = filter_window(lg, DAY, DAY)
    assert kept.empty and dropped == 1


def test_incomplete_statement_guard(cfg):
    st, _, meta, _ = load_statement(
        pd.DataFrame(
            [bank("B1", "2026-01-05 00:00:00", amount=None, desc="no transaction found")], columns=S_COLS
        ),
        cfg,
    )
    lg, *_ = load_ledger(pd.DataFrame([led("L1", T)], columns=L_COLS), cfg)
    suspicious, reasons = statement_looks_incomplete(st, lg, DAY, meta, cfg)
    assert suspicious and len(reasons) == 2


def test_empty_inputs_do_not_crash(run):
    assert run([], []).empty


def test_detect_amount_mismatch_pairs_single_bank_and_ledger():
    """One bank-only + one ledger-only with the same reference -> pair."""
    from ledger_recon.core import detect_amount_mismatch

    df = pd.DataFrame(
        [
            {
                "reference": "R1",
                "amount_minor": 150000,
                "occurrence": 1,
                "bank_txn_id": "B1",
                "ledger_id": None,
                "Match?": "Unrecon",
            },
            {
                "reference": "R1",
                "amount_minor": 155000,
                "occurrence": 1,
                "bank_txn_id": None,
                "ledger_id": "L1",
                "Match?": "Unrecon",
            },
        ]
    )
    mask, delta = detect_amount_mismatch(df)
    assert mask.tolist() == [True, True]
    assert delta.tolist() == [5000, 5000]


def test_detect_amount_mismatch_ignores_ambiguous_multiples():
    """Two bank-only + one ledger-only -> pairing would be a guess."""
    from ledger_recon.core import detect_amount_mismatch

    df = pd.DataFrame(
        [
            {
                "reference": "R1",
                "amount_minor": 150000,
                "occurrence": 1,
                "bank_txn_id": "B1",
                "ledger_id": None,
                "Match?": "Unrecon",
            },
            {
                "reference": "R1",
                "amount_minor": 152000,
                "occurrence": 1,
                "bank_txn_id": "B2",
                "ledger_id": None,
                "Match?": "Unrecon",
            },
            {
                "reference": "R1",
                "amount_minor": 155000,
                "occurrence": 1,
                "bank_txn_id": None,
                "ledger_id": "L1",
                "Match?": "Unrecon",
            },
        ]
    )
    mask, delta = detect_amount_mismatch(df)
    assert mask.tolist() == [False, False, False]
    assert delta.isna().all()


def test_detect_amount_mismatch_ignores_recon_rows():
    """Already-reconciled rows are never touched."""
    from ledger_recon.core import detect_amount_mismatch

    df = pd.DataFrame(
        [
            {
                "reference": "R1",
                "amount_minor": 150000,
                "occurrence": 1,
                "bank_txn_id": "B1",
                "ledger_id": "L1",
                "Match?": "Recon",
            },
        ]
    )
    mask, delta = detect_amount_mismatch(df)
    assert mask.tolist() == [False]
    assert delta.isna().all()


def test_amount_mismatch_replaces_case_label_on_both_rows(run):
    """Both rows of an amount mismatch pair get the same informative case."""
    r = run([bank("B1", T, amount=150000)], [led("L1", T, amount=155000)])
    bank_row = r[r["bank_txn_id"] == "B1"].iloc[0]
    ledger_row = r[r["ledger_id"] == "L1"].iloc[0]
    assert bank_row["Amount_Mismatch"] and ledger_row["Amount_Mismatch"]
    assert "AMOUNT MISMATCH" in bank_row["case"]
    assert "AMOUNT MISMATCH" in ledger_row["case"]
    assert "bank 15000000 vs ledger 15500000" in bank_row["case"]
    assert "delta +500000" in bank_row["case"]
    # Both rows share the same core label; the bank-side row may carry an
    # extra " | username auto-filled ..." suffix when the ledger knows the
    # owner. The ledger-side row does not need that suffix.
    core_label = "AMOUNT MISMATCH (bank 15000000 vs ledger 15500000, delta +500000)"
    assert bank_row["case"].startswith(core_label)
    assert ledger_row["case"] == core_label


def test_amount_mismatch_populates_delta_column(run):
    """Amount_Delta holds the signed ledger - bank difference in minor units."""
    r = run([bank("B1", T, amount=150000)], [led("L1", T, amount=155000)])
    assert r["Amount_Delta"].notna().all()
    assert (r["Amount_Delta"] == 500000).all()
    assert summarize(r)["amount_mismatch_rows"] == 2


def test_amount_mismatch_preserves_username_note(run):
    """The auto-fill note is kept after the label is replaced."""
    r = run([bank("B1", T, amount=150000)], [led("L1", T, amount=155000, user="merchant_9")])
    bank_row = r[r["bank_txn_id"] == "B1"].iloc[0]
    assert "AMOUNT MISMATCH" in bank_row["case"]
    assert "auto-filled" in bank_row["case"]
