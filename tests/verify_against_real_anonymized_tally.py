"""Verify tally_xml_parser.py against the REAL (anonymized) Tally exports.

Standalone (not part of `unittest discover`, like verify_large_split_utf16_
files.py) because it depends on the gitignored `real_sample_data/` files --
the structure-preserving anonymized copies of a real company's Tally exports
produced by anonymize_tally_xml.py (see repo root). Every structural pattern
these files exercise is ALSO covered by synthetic fixtures in
tests/test_tally_xml_parser.py; this script proves the parser handles the
patterns as they actually co-occur at real scale, and self-skips (exit 0) when
the real files aren't present, so it never breaks a checkout that lacks them.

Run:  python3 tests/verify_against_real_anonymized_tally.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tally_xml_parser import (  # noqa: E402
    PROFIT_AND_LOSS_PARENT_GROUPS,
    parse_tally_xml,
    parse_tally_xml_data,
    parse_tally_xml_data_multi,
)
from schemas.tally_data import TallyData  # noqa: E402

REAL_DIR = Path(__file__).resolve().parent.parent / "real_sample_data"
MASTERS = ["ANDERI CAFE Master 01.04.25.xml", "ANDERI CAFE Master 31.03.26.xml"]
TRANSACTIONS = ["andheri cafe trnx 24-25.xml", "andheri cafe trnx25-26  .xml"]
# masters paired with the transactions file of the matching financial year
PAIRS = [
    ("ANDERI CAFE Master 01.04.25.xml", "andheri cafe trnx25-26  .xml"),
    ("ANDERI CAFE Master 31.03.26.xml", "andheri cafe trnx 24-25.xml"),
]

failures = []


def check(cond: bool, msg: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {msg}")
    if not cond:
        failures.append(msg)


def pl_count(data: TallyData) -> int:
    return sum(1 for n in data.ledgers if data.resolve_top_level_group(n) in PROFIT_AND_LOSS_PARENT_GROUPS)


def main() -> int:
    if not REAL_DIR.is_dir() or not any((REAL_DIR / f).exists() for f in MASTERS):
        print(f"real_sample_data/ not populated at {REAL_DIR} -- skipping (this is expected on a checkout without the gitignored real files).")
        return 0

    print("=== masters files, standalone (parse_tally_xml_data) ===")
    for name in MASTERS:
        p = REAL_DIR / name
        if not p.exists():
            continue
        t0 = time.time()
        data = parse_tally_xml_data(p.read_bytes())
        check(len(data.ledgers) > 0, f"{name}: parsed {len(data.ledgers)} ledgers, {len(data.groups)} groups, "
                                     f"{len(data.vouchers)} vouchers ({time.time()-t0:.1f}s)")
        # A ledger with a NON-EMPTY parent must never resolve to '' (that was
        # the built-in-as-master overshoot bug). A ledger with a genuinely
        # empty <PARENT/> (e.g. "Profit & Loss A/c") legitimately resolves to
        # '' -- it has no parent group at all -- so it's excluded here.
        empties = [n for n in data.ledgers
                   if data.ledgers[n].parent and data.resolve_top_level_group(n) == ""]
        check(not empties, f"{name}: no non-empty-parent ledger resolves top-level group to '' "
                           f"(fix for built-in-as-master); offenders={empties[:3]}")
        # TrialBalance (P&L filtering) works and excludes P&L ledgers
        tb = parse_tally_xml(p.read_bytes())
        check(len(tb.ledgers) == len(data.ledgers) - pl_count(data),
              f"{name}: TrialBalance has {len(tb.ledgers)} balance-sheet ledgers "
              f"(= {len(data.ledgers)} total - {pl_count(data)} P&L)")

    print("\n=== transactions files, standalone (self-contained: embedded masters + vouchers) ===")
    for name in TRANSACTIONS:
        p = REAL_DIR / name
        if not p.exists():
            continue
        t0 = time.time()
        data = parse_tally_xml_data(p.read_bytes())
        check(len(data.vouchers) > 0 and len(data.ledgers) > 0,
              f"{name}: parsed {len(data.ledgers)} ledgers, {len(data.vouchers)} vouchers ({time.time()-t0:.1f}s)")
        # every leg resolves to a known master (referential integrity)
        known = set(data.ledgers)
        dangling = {leg.ledger_name for v in data.vouchers for leg in v.legs if leg.ledger_name not in known}
        check(not dangling, f"{name}: all voucher legs resolve to a master; dangling={list(dangling)[:3]}")
        # every voucher balances (sum-to-zero held even with invoice mode)
        unbalanced = sum(1 for v in data.vouchers if sum((l.amount for l in v.legs)) != 0)
        check(unbalanced == 0, f"{name}: all {len(data.vouchers)} vouchers balance to zero")

    print("\n=== masters + transactions merged (parse_tally_xml_data_multi) ===")
    for mname, tname in PAIRS:
        mp, tp = REAL_DIR / mname, REAL_DIR / tname
        if not (mp.exists() and tp.exists()):
            continue
        t0 = time.time()
        data = parse_tally_xml_data_multi([mp.read_bytes(), tp.read_bytes()])
        check(len(data.ledgers) > 0 and len(data.vouchers) > 0,
              f"{mname} + {tname}: merged to {len(data.ledgers)} ledgers, {len(data.vouchers)} vouchers "
              f"({time.time()-t0:.1f}s)")

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s) failed")
        return 1
    print("All real-file checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
