"""HARD RULE #4 final-validation harness for
checks/suspense_account_scrutiny.py -- proves the full parse -> compute ->
check pipeline end-to-end on real Tally XML.

Runs the check *unmodified* against every company directory produced by
data-synthesizer's Tally XML generator (each containing tally_export.xml
and answer_key.json), parsing tally_export.xml via
tally_xml_parser.parse_tally_xml_data_file. The parser has no concept of
"errors" -- it just extracts ledger masters and vouchers faithfully; only
the check's own Suspense-Account-posting logic decides what's flagged.

Programmatically asserts:
1. Every injected error in the answer key (identified by its
   phantom_voucher_number) is flagged by the check (recall -- no missed
   errors).
2. No voucher is flagged that isn't a phantom voucher from the answer key
   (precision -- no false positives).
3. Each flagged result's amount matches the answer key's recorded
   abs(delta) (within 1 paise).
4. Every flagged result carries all four HARD RULE #6 structured
   explanation fields, each non-empty.

Standalone script, not a unittest module (needs the sibling data-synthesizer
repo's sample output on disk) -- same reasoning as
verify_against_data_synthesizer.py and verify_tally_xml_against_data_synthesizer.py.

Usage:
    python3 tests/verify_suspense_account_scrutiny_against_data_synthesizer.py [samples_root]

Defaults to ../../data-synthesizer/samples/tally_xml relative to this
project.
"""
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from checks.suspense_account_scrutiny import run_check
from tally_xml_parser import parse_tally_xml_data_file

AMOUNT_EPSILON = Decimal("0.01")
FLAGGED_DETAIL_FIELDS = ("finding", "potential_implication", "recommended_manual_check", "why_correction_matters")


def verify_company(company_dir: Path) -> list:
    """Returns a list of human-readable failure strings; empty means pass."""
    failures = []

    xml_path = company_dir / "tally_export.xml"
    answer_key_path = company_dir / "answer_key.json"

    if not (xml_path.exists() and answer_key_path.exists()):
        return [f"missing one or more expected files in {company_dir}"]

    with open(answer_key_path) as f:
        answer_key = json.load(f)

    tally_data = parse_tally_xml_data_file(str(xml_path))
    results = run_check(tally_data)

    expected_by_voucher = {
        e["phantom_voucher_number"]: (e["ledger_name"], Decimal(str(e["delta"])).copy_abs())
        for e in answer_key["injected_errors"]
    }

    actual_flags = {
        r.source_reference.voucher_number: (r.source_reference.ledger, r.amount)
        for r in results
        if r.status == "flagged"
    }
    actual_insufficient = [r for r in results if r.status == "insufficient_data"]

    missed = set(expected_by_voucher) - set(actual_flags)
    for vn in sorted(missed):
        ledger_name, _ = expected_by_voucher[vn]
        failures.append(f"MISSED expected error on '{ledger_name}' (voucher {vn}) -- not flagged")

    unexpected = set(actual_flags) - set(expected_by_voucher)
    for vn in sorted(unexpected):
        ledger_name, _ = actual_flags[vn]
        failures.append(f"FALSE POSITIVE: voucher '{vn}' on '{ledger_name}' flagged but not in answer key")

    if actual_insufficient:
        failures.append(f"UNEXPECTED insufficient_data results: {len(actual_insufficient)}")

    for vn, (expected_ledger, expected_delta) in expected_by_voucher.items():
        if vn not in actual_flags:
            continue  # already reported as MISSED above
        actual_ledger, actual_amount = actual_flags[vn]
        if actual_ledger != expected_ledger:
            failures.append(f"LEDGER MISMATCH on voucher '{vn}': check reported '{actual_ledger}', answer key says '{expected_ledger}'")
        if (actual_amount - expected_delta).copy_abs() > AMOUNT_EPSILON:
            failures.append(
                f"AMOUNT MISMATCH on voucher '{vn}' ('{expected_ledger}'): check reported {actual_amount}, "
                f"answer key says {expected_delta}"
            )

    for r in results:
        if r.status != "flagged":
            continue
        for field_name in FLAGGED_DETAIL_FIELDS:
            value = getattr(r, field_name)
            if not value or not value.strip():
                failures.append(
                    f"HARD RULE #6 VIOLATION on voucher '{r.source_reference.voucher_number}': "
                    f"{field_name} is missing or empty"
                )

    return failures


def main():
    default_root = Path(__file__).resolve().parent.parent.parent / "data-synthesizer" / "samples" / "tally_xml"
    samples_root = Path(sys.argv[1]) if len(sys.argv) > 1 else default_root

    if not samples_root.exists():
        print(f"Samples root not found: {samples_root}")
        print("Pass a samples directory as an argument, e.g.:")
        print("  python3 tests/verify_suspense_account_scrutiny_against_data_synthesizer.py /path/to/samples/tally_xml")
        sys.exit(2)

    company_dirs = sorted(p for p in samples_root.iterdir() if p.is_dir())
    if not company_dirs:
        print(f"No company directories found under {samples_root}")
        sys.exit(2)

    any_failures = False
    for company_dir in company_dirs:
        failures = verify_company(company_dir)
        status = "FAIL" if failures else "PASS"
        print(f"[{status}] {company_dir.name}")
        for f in failures:
            print(f"    - {f}")
            any_failures = True

    print()
    if any_failures:
        print("Verification FAILED -- do not treat this check as final.")
        sys.exit(1)
    else:
        print(f"Verification PASSED across {len(company_dirs)} companies.")


if __name__ == "__main__":
    main()
