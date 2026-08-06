"""Check: Suspense Account Scrutiny.

CLI usage (see checks/opening_balance_vs_prior_year_closing.py's own "CLI
usage" note for why -- same reasoning applies here):
    python3 -m checks.suspense_account_scrutiny tally_export.xml

Status: FINAL (per CLAUDE.md HARD RULE #4). Validated by
tests/verify_suspense_account_scrutiny_against_data_synthesizer.py against 5
real data-synthesizer Tally XML sample companies (2 clean, 3 with 2/3/4
deliberately injected errors) -- every injected error was flagged, no ledger
was flagged that wasn't in the answer key, every reported amount matched the
answer key's recorded delta to the paisa, and every flagged result carries
all four HARD RULE #6 structured explanation fields, each non-empty.

Why this check exists (and why it's not opening_balance_vs_prior_year_closing.py)
--------------------------------------------------------------------------------
Building tally_xml_parser.py surfaced a real limitation (see that module's
"KNOWN LIMITATION" docstring section): opening_balance_vs_prior_year_closing.py
tests a same-point-in-time continuity fact (this year's opening should equal
last year's closing). A ledger's *closing* balance computed from a full year
of realistic, uncorrelated trading vouchers will legitimately differ from its
opening balance for any genuinely active ledger -- that's real business
activity, not a discrepancy, and forcing that comparison through the existing
check produces false positives on nearly every active ledger. That check
should stay CSV/stated-balance-focused; it was not touched for this.

This check instead targets something Tally XML's voucher-level detail
actually makes newly possible to test deterministically: real CA audit
practice always scrutinizes any activity posted through a Suspense Account,
because -- by definition -- an entry sitting in Suspense hasn't yet been
matched to its proper ledger/explanation. `data-synthesizer`'s Tally XML
generator models this directly: every deliberately-injected error is an
extra Journal voucher adjusting the target ledger against a dedicated
"Suspense Account" counter-ledger (see that project's
generators/tally_xml/generate_tally_xml.py, "Error injection design"), and
ordinary/legitimate vouchers never touch Suspense Account at all -- so
"any posting through Suspense Account" is a clean, deterministic,
100%-precision signal against that test data, and a well-established real
audit heuristic independent of this specific generator.

ASSUMPTION (flagged per CLAUDE.md HARD RULE #5): this check recognizes a
Suspense ledger by name only -- SUSPENSE_LEDGER_NAMES below, matched
case-insensitively after stripping whitespace. A client whose books use a
differently-named suspense/unreconciled-items ledger (e.g. "Reconciliation
A/c") would not be caught. Extending the name list, or matching by Tally's
built-in "Suspense A/c" primary group instead of by name, is the natural
next step if this becomes a real limitation against actual client data.

KNOWN GAP: if a voucher's legs are *all* on designated Suspense ledgers
(e.g. a transfer between two differently-named suspense ledgers), there is
no non-Suspense leg to report a finding against, so nothing is flagged for
that voucher. Not exercised by the current test data; worth revisiting if a
real Tally export does this.

Data requirements (see checks/requirements.py, checks/registry.py, coordinator.py)
------------------------------------------------------------------------------------
- current_year_tally_data: DocumentScope.VERSION_SCOPED -- the specific
  version being scrutinized's Tally data (ledger masters + vouchers). No
  prior-year document is needed; this check only reasons about postings
  within the one file.

confidence_score
-----------------
Always 1.0 -- fully deterministic name/structure matching (HARD RULE #1),
same rationale as opening_balance_vs_prior_year_closing.py.

status semantics
------------------
"flagged": one result per (voucher, non-Suspense leg) pair found -- each is
independently traceable to a specific voucher number and date, which is why
this check reports per-posting rather than per-ledger (unlike
opening_balance_vs_prior_year_closing.py, a single ledger can accumulate
more than one Suspense-routed posting across the year).
"pass": a single summary result when the file has no postings through any
designated Suspense ledger at all.
"insufficient_data": reserved for when the check cannot run at all -- an
unreadable, missing, or unparseable Tally XML file. See run_check_from_file.
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from typing import List

from checks.requirements import DataRequirement
from checks.results import CheckResult, SourceReference
from schemas.enums import DocumentScope, DocumentType
from schemas.tally_data import TallyData
from tally_xml_parser import TallyXmlParseError, parse_tally_xml_data_file

CHECK_ID = "suspense_account_scrutiny"
CHECK_NAME = "Suspense Account Scrutiny"

SUSPENSE_LEDGER_NAMES = {"suspense account", "suspense a/c", "suspense"}

DATA_REQUIREMENTS: List[DataRequirement] = [
    DataRequirement(
        role="current_year_tally_data",
        document_type=DocumentType.TALLY_DATA,
        scope=DocumentScope.VERSION_SCOPED,
        description="Current year (this version's) Tally data -- ledger masters and vouchers",
    ),
]


def _format_inr(amount: Decimal) -> str:
    return f"Rs {amount:,.2f}"


def _is_suspense_ledger(name: str) -> bool:
    return name.strip().lower() in SUSPENSE_LEDGER_NAMES


def _flagged_result(ledger: str, voucher_number: str, date: str, amount: Decimal, finding: str) -> CheckResult:
    return CheckResult(
        check_id=CHECK_ID,
        status="flagged",
        confidence_score=1.0,
        description=finding,
        amount=amount,
        source_reference=SourceReference(ledger=ledger, voucher_number=voucher_number, date=date),
        finding=finding,
        potential_implication=(
            "Postings routed through a Suspense Account are, by definition, not yet "
            "substantiated by their proper double-entry counterpart. This commonly "
            "indicates an unreconciled difference being parked until its cause is "
            "identified, a placeholder entry made to force the books to balance, or "
            "(less commonly) a manual adjustment made without the supporting "
            "documentation that would justify posting it to a properly identified "
            "ledger."
        ),
        recommended_manual_check=(
            "Request the underlying supporting document or business explanation for "
            "this specific voucher from the client's accountant, and confirm whether "
            "it should have been posted directly to a properly identified ledger "
            "instead of Suspense Account. If no satisfactory explanation is provided, "
            "treat the adjustment as unsupported and flag the affected ledger's "
            "balance as requiring correction."
        ),
        why_correction_matters=(
            "Any balance sitting in or routed through a Suspense Account is, by "
            "definition, an unresolved item in the books. Left uninvestigated it can "
            "mask a genuine error, an unrecorded transaction, or a deliberate "
            "manipulation of the affected ledger's balance, and undermines confidence "
            "in every subsequent balance computed from that ledger."
        ),
    )


def run_check(tally_data: TallyData) -> List[CheckResult]:
    """Scans every voucher in `tally_data` for postings through a
    designated Suspense ledger (see SUSPENSE_LEDGER_NAMES) and flags the
    other, non-Suspense leg of each such voucher as an unsupported
    adjustment requiring review.
    """
    suspense_names = {name for name in tally_data.ledgers if _is_suspense_ledger(name)}

    results: List[CheckResult] = []
    for voucher in tally_data.vouchers:
        if not any(leg.ledger_name in suspense_names for leg in voucher.legs):
            continue

        suspense_leg = next(leg for leg in voucher.legs if leg.ledger_name in suspense_names)
        for leg in voucher.legs:
            if leg.ledger_name in suspense_names:
                continue
            amount = leg.amount.copy_abs()
            finding = (
                f"Ledger '{leg.ledger_name}' was adjusted by {_format_inr(amount)} via voucher "
                f"'{voucher.voucher_number}' dated {voucher.date} ({voucher.vch_type}), posted "
                f"through Suspense ledger '{suspense_leg.ledger_name}'. Narration: \"{voucher.narration}\"."
            )
            results.append(_flagged_result(leg.ledger_name, voucher.voucher_number, voucher.date, amount, finding))

    if not results:
        results.append(CheckResult(
            check_id=CHECK_ID,
            status="pass",
            confidence_score=1.0,
            description=(
                f"No postings through a designated Suspense ledger were found -- "
                f"{len(tally_data.vouchers)} vouchers examined, nothing to flag."
            ),
            amount=None,
            source_reference=SourceReference(),
        ))

    results.sort(key=lambda r: (r.source_reference.date or "", r.source_reference.voucher_number or ""))
    return results


def run_check_from_db(client_id: str, fy: str, version_id: str) -> List[CheckResult]:
    """Same as run_check_from_file, but loads TallyData from Postgres via
    db/queries.py instead of parsing a Tally XML file (see CLAUDE.md's
    "Postgres data layer" section, "Migrating the checks" subsection).
    run_check() itself is completely unchanged -- this function only swaps
    the input source. Returns status="insufficient_data" (same contract as
    run_check_from_file) if no ledger masters are stored for this
    client/fy/version, or if the database can't be reached -- both mean
    "the check cannot run", the same class of failure a missing/unparseable
    XML file would be. Zero vouchers alone is NOT treated as
    insufficient_data (a genuinely quiet period is valid input, same as an
    XML file with masters but no vouchers) -- only zero ledger masters is,
    since a real Tally export always has at least some ledgers even with no
    activity for the period.
    """
    # Imported lazily -- see the identical note in
    # opening_balance_vs_prior_year_closing.run_check_from_db.
    import psycopg

    from db.queries import get_tally_data

    try:
        tally_data = get_tally_data(client_id, fy, version_id)
    except psycopg.Error as e:
        return [CheckResult(
            check_id=CHECK_ID,
            status="insufficient_data",
            confidence_score=1.0,
            description=f"Could not load Tally data from the database for client '{client_id}', FY {fy}, version '{version_id}': {e}",
            amount=None,
            source_reference=SourceReference(),
        )]
    if not tally_data.ledgers:
        return [CheckResult(
            check_id=CHECK_ID,
            status="insufficient_data",
            confidence_score=1.0,
            description=f"No Tally ledger masters found in the database for client '{client_id}', FY {fy}, version '{version_id}'.",
            amount=None,
            source_reference=SourceReference(),
        )]

    return run_check(tally_data)


def run_check_from_file(tally_xml_path: str) -> List[CheckResult]:
    """Loads the Tally XML file and runs the check. If it can't be read or
    parsed, returns a single check-level status="insufficient_data" result
    (same pattern as opening_balance_vs_prior_year_closing.run_check_from_files)
    rather than letting the exception propagate.
    """
    try:
        tally_data = parse_tally_xml_data_file(tally_xml_path)
    except (TallyXmlParseError, OSError) as e:
        return [CheckResult(
            check_id=CHECK_ID,
            status="insufficient_data",
            confidence_score=1.0,
            description=f"Could not load Tally XML file '{tally_xml_path}': {e}",
            amount=None,
            source_reference=SourceReference(),
        )]

    return run_check(tally_data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tally_xml_path", help="Path to a Tally XML export")
    args = parser.parse_args()

    results = run_check_from_file(args.tally_xml_path)

    print(json.dumps([r.to_dict() for r in results], indent=2))

    counts = {"pass": 0, "flagged": 0, "insufficient_data": 0}
    for r in results:
        counts[r.status] += 1
    print(
        f"\n{len(results)} results: "
        f"{counts['pass']} pass, {counts['flagged']} flagged, "
        f"{counts['insufficient_data']} insufficient_data",
    )


if __name__ == "__main__":
    main()
