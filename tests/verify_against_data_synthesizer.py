"""HARD RULE #4 final-validation harness for
checks/opening_balance_vs_prior_year_closing.py.

Runs the check against every company directory produced by the
`data-synthesizer` project's trial balance generator (each containing
prior_year_closing_trial_balance.csv, current_year_opening_trial_balance.csv,
and answer_key.json) and programmatically asserts:

1. Every ledger listed in the answer key's injected_errors is flagged by the
   check (recall -- no missed errors).
2. No ledger NOT listed in injected_errors is flagged (precision -- no false
   positives).
3. Each flagged ledger's reported discrepancy amount matches the answer
   key's recorded delta (within 1 paise, for float/Decimal rounding).
4. Every flagged result carries all four HARD RULE #6 structured
   explanation fields (finding, potential_implication,
   recommended_manual_check, why_correction_matters), each non-empty.

This is a standalone script, not a unittest module, because it needs a
samples-root directory argument and is meant to be run deliberately against
real generator output -- not on every `python3 -m unittest discover`.

Usage:
    python3 tests/verify_against_data_synthesizer.py [samples_root]

Defaults to ../../data-synthesizer/samples/trial_balance relative to this
project, since that's where the sibling data-synthesizer project's sample
output lives on this machine. Exits non-zero if any company fails
verification, so it can be used as a CI gate later.
"""
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from checks.opening_balance_vs_prior_year_closing import run_check
from schemas.trial_balance import TrialBalance

AMOUNT_EPSILON = Decimal("0.01")
FLAGGED_DETAIL_FIELDS = ("finding", "potential_implication", "recommended_manual_check", "why_correction_matters")


def verify_company(company_dir: Path) -> list:
    """Returns a list of human-readable failure strings; empty means pass."""
    failures = []

    prior_path = company_dir / "prior_year_closing_trial_balance.csv"
    current_path = company_dir / "current_year_opening_trial_balance.csv"
    answer_key_path = company_dir / "answer_key.json"

    if not (prior_path.exists() and current_path.exists() and answer_key_path.exists()):
        return [f"missing one or more expected files in {company_dir}"]

    with open(answer_key_path) as f:
        answer_key = json.load(f)

    prior_tb = TrialBalance.from_csv(str(prior_path))
    current_tb = TrialBalance.from_csv(str(current_path))
    results = run_check(prior_tb, current_tb)

    expected_flags = {
        e["ledger_name"]: Decimal(str(e["delta"])).copy_abs()
        for e in answer_key["injected_errors"]
    }

    actual_flags = {
        r.source_reference.ledger: r.amount
        for r in results
        if r.status == "flagged"
    }
    actual_insufficient = {
        r.source_reference.ledger
        for r in results
        if r.status == "insufficient_data"
    }

    missed = set(expected_flags) - set(actual_flags)
    for ledger in sorted(missed):
        failures.append(f"MISSED expected error on '{ledger}' -- not flagged")

    unexpected = set(actual_flags) - set(expected_flags)
    for ledger in sorted(unexpected):
        failures.append(f"FALSE POSITIVE: '{ledger}' flagged but not in answer key")

    if actual_insufficient:
        failures.append(f"UNEXPECTED insufficient_data results: {sorted(actual_insufficient)}")

    for ledger, expected_delta in expected_flags.items():
        if ledger not in actual_flags:
            continue  # already reported as MISSED above
        actual_delta = actual_flags[ledger]
        if (actual_delta - expected_delta).copy_abs() > AMOUNT_EPSILON:
            failures.append(
                f"AMOUNT MISMATCH on '{ledger}': check reported {actual_delta}, "
                f"answer key says {expected_delta}"
            )

    for r in results:
        if r.status != "flagged":
            continue
        for field_name in FLAGGED_DETAIL_FIELDS:
            value = getattr(r, field_name)
            if not value or not value.strip():
                failures.append(
                    f"HARD RULE #6 VIOLATION on '{r.source_reference.ledger}': "
                    f"{field_name} is missing or empty"
                )

    return failures


def main():
    default_root = Path(__file__).resolve().parent.parent.parent / "data-synthesizer" / "samples" / "trial_balance"
    samples_root = Path(sys.argv[1]) if len(sys.argv) > 1 else default_root

    if not samples_root.exists():
        print(f"Samples root not found: {samples_root}")
        print("Pass a samples directory as an argument, e.g.:")
        print("  python3 tests/verify_against_data_synthesizer.py /path/to/samples/trial_balance")
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
