"""Check: Opening Balance vs Prior Year Closing Balance.

Reconciles a "current year opening" trial balance against the same company's
"prior year closing" trial balance, ledger by ledger. This is a basic
accounting continuity check: for a permanent (balance-sheet) ledger, the
balance a new financial year opens with must equal the balance the prior
financial year closed with. Any deviation means either a data-entry error, an
unrecorded adjustment, or the books were tampered with between year-end close
and year-open.

Input format
------------
Both input files are CSVs with the header `Ledger Name,Group,Debit,Credit`,
one row per ledger, matching the shape produced by the `data-synthesizer`
project's trial balance generator. Exactly one of Debit/Credit is expected to
be non-zero per row (the ledger's normal balance side); this check does not
assume that, though -- it computes a signed net balance
(`debit - credit`) per ledger, which is correct for that format and also
degrades gracefully if a future input has both columns populated.

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
one is "the real" balance would be ambiguous.

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
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import List, Optional

CHECK_ID = "opening_balance_vs_prior_year_closing"

DEFAULT_TOLERANCE = Decimal("1.00")  # see module docstring "Tolerance"


@dataclass
class LedgerBalance:
    name: str
    group: str
    debit: Decimal
    credit: Decimal

    @property
    def net_balance(self) -> Decimal:
        return self.debit - self.credit


@dataclass
class SourceReference:
    ledger: Optional[str] = None
    voucher_number: Optional[str] = None  # not applicable to this check
    date: Optional[str] = None  # not applicable to this check


@dataclass
class CheckResult:
    check_id: str
    status: str  # "pass" | "flagged" | "insufficient_data"
    confidence_score: float
    description: str
    amount: Optional[Decimal]
    source_reference: SourceReference = field(default_factory=SourceReference)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["amount"] = float(self.amount) if self.amount is not None else None
        return d


def load_trial_balance_csv(path: str) -> List[LedgerBalance]:
    """Parses a trial balance CSV in the `Ledger Name,Group,Debit,Credit`
    shape into LedgerBalance records. Raises ValueError on a malformed row
    or a duplicate ledger name within the file.
    """
    rows: List[LedgerBalance] = []
    seen_names = set()

    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        expected_columns = {"Ledger Name", "Group", "Debit", "Credit"}
        if reader.fieldnames is None or not expected_columns.issubset(set(reader.fieldnames)):
            raise ValueError(
                f"{path}: expected columns {sorted(expected_columns)}, "
                f"got {reader.fieldnames}"
            )

        for line_num, row in enumerate(reader, start=2):  # header is line 1
            name = row["Ledger Name"].strip()
            if not name:
                raise ValueError(f"{path}:{line_num}: empty Ledger Name")
            if name in seen_names:
                raise ValueError(
                    f"{path}:{line_num}: duplicate ledger name '{name}' -- "
                    "ambiguous which balance is authoritative, refusing to guess"
                )
            seen_names.add(name)

            try:
                debit = Decimal(row["Debit"].strip())
                credit = Decimal(row["Credit"].strip())
            except InvalidOperation as e:
                raise ValueError(f"{path}:{line_num}: non-numeric Debit/Credit for '{name}'") from e

            rows.append(LedgerBalance(name=name, group=row["Group"].strip(), debit=debit, credit=credit))

    return rows


def _format_inr(amount: Decimal) -> str:
    return f"Rs {amount:,.2f}"


def run_check(
    prior_year_rows: List[LedgerBalance],
    current_year_rows: List[LedgerBalance],
    tolerance: Decimal = DEFAULT_TOLERANCE,
) -> List[CheckResult]:
    prior_by_name = {r.name: r for r in prior_year_rows}
    current_by_name = {r.name: r for r in current_year_rows}

    all_names = sorted(set(prior_by_name) | set(current_by_name))
    results: List[CheckResult] = []

    for name in all_names:
        prior = prior_by_name.get(name)
        current = current_by_name.get(name)

        if prior is not None and current is None:
            results.append(CheckResult(
                check_id=CHECK_ID,
                status="insufficient_data",
                confidence_score=1.0,
                description=(
                    f"Ledger '{name}' has a prior year closing balance of "
                    f"{_format_inr(prior.net_balance)} but is missing entirely "
                    "from the current year opening trial balance. Cannot verify "
                    "opening balance continuity -- confirm whether this ledger "
                    "was closed/merged, or whether this is a data omission."
                ),
                amount=prior.net_balance,
                source_reference=SourceReference(ledger=name),
            ))
            continue

        if current is not None and prior is None:
            results.append(CheckResult(
                check_id=CHECK_ID,
                status="insufficient_data",
                confidence_score=1.0,
                description=(
                    f"Ledger '{name}' has a current year opening balance of "
                    f"{_format_inr(current.net_balance)} but was not present in "
                    "the prior year closing trial balance. Cannot verify opening "
                    "balance continuity -- confirm whether this is a genuinely "
                    "new ledger opened this year, or a naming mismatch with an "
                    "existing prior year ledger (this check only matches ledgers "
                    "by exact name, see module docstring)."
                ),
                amount=current.net_balance,
                source_reference=SourceReference(ledger=name),
            ))
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
            results.append(CheckResult(
                check_id=CHECK_ID,
                status="flagged",
                confidence_score=1.0,
                description=(
                    f"Opening balance for '{name}' ({_format_inr(current.net_balance)}) "
                    f"does not match prior year closing balance "
                    f"({_format_inr(prior.net_balance)}). Difference: "
                    f"{_format_inr(diff)}, exceeds the Rs {tolerance:.2f} tolerance."
                ),
                amount=diff,
                source_reference=SourceReference(ledger=name),
            ))

    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prior_year_csv", help="Path to prior year closing trial balance CSV")
    parser.add_argument("current_year_csv", help="Path to current year opening trial balance CSV")
    parser.add_argument("--tolerance", type=str, default=str(DEFAULT_TOLERANCE), help="Rupee tolerance for amount mismatches")
    args = parser.parse_args()

    prior_rows = load_trial_balance_csv(args.prior_year_csv)
    current_rows = load_trial_balance_csv(args.current_year_csv)
    results = run_check(prior_rows, current_rows, tolerance=Decimal(args.tolerance))

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
