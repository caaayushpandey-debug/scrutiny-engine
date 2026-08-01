"""Check: Opening Balance vs Prior Year Closing Balance.

CLI usage: run as a module, not as a bare script path, since this file
imports sibling packages (checks.requirements, checks.results,
schemas.trial_balance) that only resolve correctly when the project root is
on sys.path -- which `python3 -m` sets up automatically and a bare
`python3 checks/opening_balance_vs_prior_year_closing.py` does not:
    python3 -m checks.opening_balance_vs_prior_year_closing prior.csv current.csv

Status: FINAL (per CLAUDE.md HARD RULE #4). Validated by
tests/verify_against_data_synthesizer.py against 5 real data-synthesizer
sample companies (2 clean, 3 with 2/3/4 deliberately injected errors) --
every injected error was flagged, no ledger was flagged that wasn't in the
answer key, every reported discrepancy amount matched the answer key's
recorded delta to the paisa, and every flagged result carries all four HARD
RULE #6 structured explanation fields (finding, potential_implication,
recommended_manual_check, why_correction_matters), each non-empty. See that
script's output for the full record.

Reconciles a "current year opening" trial balance against the same company's
"prior year closing" trial balance, ledger by ledger. This is a basic
accounting continuity check: for a permanent (balance-sheet) ledger, the
balance a new financial year opens with must equal the balance the prior
financial year closed with. Any deviation means either a data-entry error, an
unrecorded adjustment, or the books were tampered with between year-end close
and year-open.

Data requirements (see checks/requirements.py, checks/registry.py,
coordinator.py)
-----------------------------------------------------------------
This check needs two TrialBalance documents (schemas.trial_balance) in two
different scopes -- see DATA_REQUIREMENTS below and CLAUDE.md HARD RULE #7
"Document scope model":
- prior_year_trial_balance: DocumentScope.PERIOD_SCOPED_PRIOR_YEAR -- last
  year's audited closing trial balance, uploaded once at FY setup.
- current_year_trial_balance: DocumentScope.VERSION_SCOPED -- the specific
  version being scrutinized's opening trial balance.

Ledger matching
----------------
Ledgers are matched between the two files by exact `Ledger Name` string match
(after stripping surrounding whitespace). Near-duplicate or fuzzy name
matching (e.g. typos, "HDFC Bank A/c" vs "HDFC Bank Current A/c") is
explicitly OUT OF SCOPE for this check -- a ledger renamed between years will
show up as one "missing from current year" and one "missing from prior year"
result, not as a single matched-but-renamed ledger. That's a limitation, not
a bug; a name-similarity check would be a separate, fuzzier check.

Duplicate ledger names within a single input file are treated as a data
error and raise ValueError rather than being silently resolved, since which
one is "the real" balance would be ambiguous (see schemas/trial_balance.py).

ASSUMPTION (flagged per CLAUDE.md HARD RULE #5, though this is an accounting
convention rather than a tax-law question specifically): this check assumes
every ledger in the input files is a *permanent* (balance-sheet: assets,
liabilities, capital, reserves) ledger that is expected to carry forward
year-over-year. Profit & Loss trading/expense ledgers (Sales, Purchases,
direct/indirect expenses) correctly reset to zero at year-end and do NOT
carry forward -- if such ledgers were ever included in the input, this check
would incorrectly flag their absence in the new year as a discrepancy. This
is safe today because `data-synthesizer`'s generator only produces
balance-sheet ledgers, but must be revisited if that scope changes.

Tolerance
---------
A configurable tolerance (default Rs 1.00) absorbs rounding noise, not real
discrepancies. Rationale: real bookkeeping/export rounding differences
(paise-level differences from independent rounding in different systems) are
typically a few paise to a few rupees. `data-synthesizer`'s own deliberate
error injection uses a documented minimum delta of Rs 250 (see that
project's generator), specifically so that a small tolerance here can never
mask a real injected error. A tolerance far below that floor (Rs 1.00) is
chosen so it only ever absorbs genuine rounding noise, never a real
discrepancy from the test data this check is validated against.

confidence_score
-----------------
Always 1.0. This check is fully deterministic (HARD RULE #1: plain Python
comparison logic, never an LLM judgment call), so there's no probabilistic
uncertainty to express -- confidence_score exists in the shared output shape
for future checks that may have genuine uncertainty (e.g. fuzzy matching).

status semantics: flagged vs insufficient_data
------------------------------------------------
A ledger present in only one of the two files is a real audit finding (an
unexplained appearing/disappearing balance), not a data-quality problem with
the input -- so it is status="flagged" with the full HARD RULE #6 structured
explanation, same as an amount mismatch.

status="insufficient_data" is reserved for when the check cannot run at all:
an unreadable, missing, or unparseable input file. See run_check_from_files,
which is where that's handled -- run_check itself assumes it has already
been given well-formed TrialBalance objects and never returns
insufficient_data.
"""
from __future__ import annotations

import argparse
import json
from decimal import Decimal
from typing import List

from checks.requirements import DataRequirement
from checks.results import CheckResult, SourceReference
from schemas.enums import DocumentScope, DocumentType
from schemas.trial_balance import LedgerBalance, TrialBalance

CHECK_ID = "opening_balance_vs_prior_year_closing"
CHECK_NAME = "Opening Balance vs Prior Year Closing Balance"

DEFAULT_TOLERANCE = Decimal("1.00")  # see module docstring "Tolerance"

DATA_REQUIREMENTS: List[DataRequirement] = [
    DataRequirement(
        role="prior_year_trial_balance",
        document_type=DocumentType.TRIAL_BALANCE,
        scope=DocumentScope.PERIOD_SCOPED_PRIOR_YEAR,
        description="Prior year closing trial balance",
    ),
    DataRequirement(
        role="current_year_trial_balance",
        document_type=DocumentType.TRIAL_BALANCE,
        scope=DocumentScope.VERSION_SCOPED,
        description="Current year (this version's) trial balance",
    ),
]


def _format_inr(amount: Decimal) -> str:
    return f"Rs {amount:,.2f}"


def _flagged_result(name: str, amount: Decimal, finding: str, potential_implication: str,
                     recommended_manual_check: str, why_correction_matters: str) -> CheckResult:
    return CheckResult(
        check_id=CHECK_ID,
        status="flagged",
        confidence_score=1.0,
        description=finding,
        amount=amount,
        source_reference=SourceReference(ledger=name),
        finding=finding,
        potential_implication=potential_implication,
        recommended_manual_check=recommended_manual_check,
        why_correction_matters=why_correction_matters,
    )


def _missing_from_current_year(name: str, prior: LedgerBalance) -> CheckResult:
    amt = _format_inr(prior.net_balance)
    return _flagged_result(
        name=name,
        amount=prior.net_balance,
        finding=(
            f"Ledger '{name}' has a prior year closing balance of {amt} but "
            "does not appear anywhere in the current year opening trial "
            "balance."
        ),
        potential_implication=(
            "Commonly indicates one of: an unrecorded disposal or settlement "
            "of the underlying asset/liability; the ledger was closed out and "
            "its balance written off or transferred without a corresponding "
            "opening entry; the ledger was renamed or merged into another "
            "ledger without being reconciled; or a data migration/export "
            "error when the current year's books were set up."
        ),
        recommended_manual_check=(
            "Check the client's opening journal entries and the prior year's "
            "audited financial statements for evidence the ledger was "
            "genuinely closed (asset disposed, loan repaid, debtor balance "
            "written off) and request the supporting documentation if so. If "
            "no such event is documented, ask the client's accountant "
            "whether the ledger was renamed or merged, and reconcile "
            "accordingly."
        ),
        why_correction_matters=(
            "An unexplained disappearing balance breaks the audit trail "
            "between years and can mask a real loss, an unauthorized "
            "write-off, or a bookkeeping error that misstates the current "
            "year's opening position and every balance computed from it."
        ),
    )


def _missing_from_prior_year(name: str, current: LedgerBalance) -> CheckResult:
    amt = _format_inr(current.net_balance)
    return _flagged_result(
        name=name,
        amount=current.net_balance,
        finding=(
            f"Ledger '{name}' has a current year opening balance of {amt} "
            "but was not present in the prior year closing trial balance."
        ),
        potential_implication=(
            "Legitimate if this is a genuinely new ledger opened this year "
            "(e.g. a new bank account, a new debtor/creditor relationship) "
            "with an opening balance correctly brought in via a journal "
            "entry. A concern if it's actually an existing prior-year ledger "
            "that was renamed, split, or re-coded without being reconciled "
            "to its old name -- in which case the balance isn't genuinely "
            "new, it has effectively dropped out of the audit trail under "
            "its previous identity."
        ),
        recommended_manual_check=(
            "Confirm with the client's accountant whether this is a "
            "genuinely new ledger and, if so, request supporting "
            "documentation for how its opening balance was determined (new "
            "loan agreement, new bank account opening statement, etc.). If "
            "it may be a renamed/re-coded ledger, compare names and amounts "
            "against the prior year list for a likely match and confirm the "
            "rename with the client."
        ),
        why_correction_matters=(
            "An unexplained new balance with no prior-year lineage may "
            "represent an undocumented opening adjustment, a duplicated "
            "ledger from a renaming error, or (less commonly) an attempt to "
            "introduce a balance into the books without proper "
            "authorization. Left unresolved it undermines confidence in the "
            "completeness of the opening trial balance as a whole."
        ),
    )


def _amount_mismatch(name: str, prior: LedgerBalance, current: LedgerBalance, diff: Decimal, tolerance: Decimal) -> CheckResult:
    return _flagged_result(
        name=name,
        amount=diff,
        finding=(
            f"Opening balance for '{name}' ({_format_inr(current.net_balance)}) "
            f"does not match prior year closing balance "
            f"({_format_inr(prior.net_balance)}). Difference: "
            f"{_format_inr(diff)}, exceeds the Rs {tolerance:.2f} tolerance."
        ),
        potential_implication=(
            "Common causes include an unrecorded partial disposal or "
            "write-off of the underlying asset/liability, an unauthorized "
            "manual adjustment to the opening balance, a data entry or "
            "migration error when the new year's books were set up, or "
            "(less commonly) a legitimate but undocumented adjustment such "
            "as a waiver or settlement that wasn't backed by a journal "
            "entry."
        ),
        recommended_manual_check=(
            "Trace the difference to a specific transaction or adjustment: "
            "check for journal entries dated at year-end or in the opening "
            "period affecting this ledger, compare against the prior year's "
            "audited closing balance confirmation, and ask the client's "
            "accountant to explain and document the adjustment. If no "
            "explanation is found, treat the difference as an unexplained "
            "variance requiring correction before relying on this ledger."
        ),
        why_correction_matters=(
            "An unreconciled opening balance undermines the reliability of "
            "every subsequent movement recorded against this ledger during "
            "the year, since all current-year additions and reductions are "
            "being tracked from an unverified starting point."
        ),
    )


def run_check(
    prior_year_trial_balance: TrialBalance,
    current_year_trial_balance: TrialBalance,
    tolerance: Decimal = DEFAULT_TOLERANCE,
) -> List[CheckResult]:
    """Compares two already-loaded TrialBalance documents. Assumes
    well-formed input (use TrialBalance.from_csv or run_check_from_files to
    get there) -- this function never returns status="insufficient_data";
    see module docstring "status semantics".
    """
    prior_by_name = {r.name: r for r in prior_year_trial_balance.ledgers}
    current_by_name = {r.name: r for r in current_year_trial_balance.ledgers}

    all_names = sorted(set(prior_by_name) | set(current_by_name))
    results: List[CheckResult] = []

    for name in all_names:
        prior = prior_by_name.get(name)
        current = current_by_name.get(name)

        if prior is not None and current is None:
            results.append(_missing_from_current_year(name, prior))
            continue

        if current is not None and prior is None:
            results.append(_missing_from_prior_year(name, current))
            continue

        # present in both -- compare net balances within tolerance
        diff = (current.net_balance - prior.net_balance).copy_abs()
        if diff <= tolerance:
            results.append(CheckResult(
                check_id=CHECK_ID,
                status="pass",
                confidence_score=1.0,
                description=(
                    f"Opening balance for '{name}' ({_format_inr(current.net_balance)}) "
                    f"matches prior year closing balance ({_format_inr(prior.net_balance)}) "
                    f"within the Rs {tolerance:.2f} tolerance."
                ),
                amount=current.net_balance,
                source_reference=SourceReference(ledger=name),
            ))
        else:
            results.append(_amount_mismatch(name, prior, current, diff, tolerance))

    return results


def run_check_from_files(
    prior_year_csv_path: str,
    current_year_csv_path: str,
    tolerance: Decimal = DEFAULT_TOLERANCE,
) -> List[CheckResult]:
    """Loads both input files and runs the check. If either file can't be
    read or parsed, returns a single check-level status="insufficient_data"
    result describing which file failed and why -- this is the one place
    this check produces insufficient_data (see module docstring "status
    semantics"), rather than letting the exception propagate and crash
    whatever is calling this check.
    """
    try:
        prior_tb = TrialBalance.from_csv(prior_year_csv_path)
    except (ValueError, OSError) as e:
        return [CheckResult(
            check_id=CHECK_ID,
            status="insufficient_data",
            confidence_score=1.0,
            description=(
                f"Could not load prior year closing trial balance file "
                f"'{prior_year_csv_path}': {e}"
            ),
            amount=None,
            source_reference=SourceReference(),
        )]

    try:
        current_tb = TrialBalance.from_csv(current_year_csv_path)
    except (ValueError, OSError) as e:
        return [CheckResult(
            check_id=CHECK_ID,
            status="insufficient_data",
            confidence_score=1.0,
            description=(
                f"Could not load current year opening trial balance file "
                f"'{current_year_csv_path}': {e}"
            ),
            amount=None,
            source_reference=SourceReference(),
        )]

    return run_check(prior_tb, current_tb, tolerance=tolerance)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prior_year_csv", help="Path to prior year closing trial balance CSV")
    parser.add_argument("current_year_csv", help="Path to current year opening trial balance CSV")
    parser.add_argument("--tolerance", type=str, default=str(DEFAULT_TOLERANCE), help="Rupee tolerance for amount mismatches")
    args = parser.parse_args()

    results = run_check_from_files(args.prior_year_csv, args.current_year_csv, tolerance=Decimal(args.tolerance))

    print(json.dumps([r.to_dict() for r in results], indent=2))

    counts = {"pass": 0, "flagged": 0, "insufficient_data": 0}
    for r in results:
        counts[r.status] += 1
    print(
        f"\n{len(results)} ledgers checked: "
        f"{counts['pass']} pass, {counts['flagged']} flagged, "
        f"{counts['insufficient_data']} insufficient_data",
    )


if __name__ == "__main__":
    main()
