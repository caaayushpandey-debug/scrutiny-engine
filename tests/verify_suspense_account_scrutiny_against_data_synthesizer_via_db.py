"""Same HARD RULE #4 verification as
verify_suspense_account_scrutiny_against_data_synthesizer.py, but proves the
Postgres-sourced path: calls
checks.suspense_account_scrutiny.run_check_from_db instead of parsing
tally_export.xml directly, against data already loaded into the local
scrutiny_engine database by db/load_sample_data.py.

Both this script and the original file-sourced one are kept -- see
verify_against_data_synthesizer_via_db.py's docstring for why. This one
exists specifically to prove run_check_from_db produces identical results to
run_check_from_file, not just that it runs.

Prerequisite: db/load_sample_data.py must have already been run. client_id =
"txml_<company_dir_name>", fy = answer_key's current_year_fy, version_id =
"v1" -- see db/load_sample_data.py's own docstring.

Usage:
    ./venv/bin/python3 tests/verify_suspense_account_scrutiny_against_data_synthesizer_via_db.py [samples_root]

Defaults to ../../data-synthesizer/samples/tally_xml.
"""
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from checks.suspense_account_scrutiny import run_check_from_db
from db.load_sample_data import VERSION_ID

AMOUNT_EPSILON = Decimal("0.01")
FLAGGED_DETAIL_FIELDS = ("finding", "potential_implication", "recommended_manual_check", "why_correction_matters")


def verify_company(company_dir: Path) -> list:
    """Returns a list of human-readable failure strings; empty means pass."""
    failures = []

    answer_key_path = company_dir / "answer_key.json"
    if not answer_key_path.exists():
        return [f"missing answer_key.json in {company_dir}"]

    with open(answer_key_path) as f:
        answer_key = json.load(f)

    client_id = f"txml_{company_dir.name}"
    fy = answer_key["current_year_fy"]

    results = run_check_from_db(client_id, fy, VERSION_ID)

    if len(results) == 1 and results[0].status == "insufficient_data":
        return [f"UNEXPECTED insufficient_data -- was this company loaded via db/load_sample_data.py? {results[0].description}"]

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
        sys.exit(2)

    company_dirs = sorted(p for p in samples_root.iterdir() if p.is_dir())
    if not company_dirs:
        print(f"No company directories found under {samples_root}")
        sys.exit(2)

    any_failures = False
    for company_dir in company_dirs:
        failures = verify_company(company_dir)
        status = "FAIL" if failures else "PASS"
        print(f"[{status}] {company_dir.name} (via Postgres)")
        for f in failures:
            print(f"    - {f}")
            any_failures = True

    print()
    if any_failures:
        print("Verification FAILED -- do not treat run_check_from_db as final.")
        sys.exit(1)
    else:
        print(f"Verification PASSED across {len(company_dirs)} companies (Postgres-sourced).")


if __name__ == "__main__":
    main()
