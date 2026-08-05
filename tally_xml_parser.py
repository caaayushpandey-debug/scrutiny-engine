"""Tally XML -> TallyData / TrialBalance parser.

Reference schema: the same Tally Import/Export XML shape documented in
`data-synthesizer`'s Tally XML generator (see that project's
generators/tally_xml/generate_tally_xml.py docstring for the exact citation
of Tally's own Developer Reference and public sample exports this is based
on) -- ENVELOPE > HEADER/BODY > IMPORTDATA > REQUESTDESC/REQUESTDATA >
TALLYMESSAGE, each containing a LEDGER master, a GROUP master, or a VOUCHER.
LEDGER uses NAME (attribute), PARENT, OPENINGBALANCE. GROUP uses NAME
(attribute) and PARENT -- Tally only emits a GROUP master for a
company-created custom group (its own built-in reserved primary groups never
get one; see schemas/tally_data.py's TallyGroupMaster and
TallyData.resolve_top_level_group). VOUCHER uses VCHTYPE (attribute),
VOUCHERNUMBER, DATE, NARRATION, and one ALLLEDGERENTRIES.LIST per leg
(LEDGERNAME, ISDEEMEDPOSITIVE, AMOUNT).

Two entry points
------------------
- `parse_tally_xml_data` returns a `schemas.tally_data.TallyData` -- ledger
  masters plus every voucher, fully preserved (voucher number, date,
  narration, every leg). Checks that need to reason about *which specific
  voucher* touched a ledger (e.g. checks/suspense_account_scrutiny.py) need
  this level of detail, not just a final number.
- `parse_tally_xml` collapses that into a `schemas.trial_balance.TrialBalance`
  (one net balance per permanent ledger) -- see "Balance-sheet vs P&L
  filtering" below. This exists for checks that only need a stated balance
  per ledger, the same shape trial_balance_csv_parser.py produces.

Why computing a closing balance is harder than reading a CSV cell
----------------------------------------------------------------------
trial_balance_csv_parser.py reads a single stated number per ledger. Tally
XML only states each ledger's OPENING balance directly -- a *closing*
balance (TallyData.closing_balance) isn't stored anywhere as a single
field; it's computed as opening balance plus the signed effect of every
voucher leg referencing that ledger, anywhere in the file.

Tally's sign convention (verified against the generator's own output, and
against real sample exports it's modelled on): a debit leg has
ISDEEMEDPOSITIVE=Yes and a *negative* AMOUNT; a credit leg has
ISDEEMEDPOSITIVE=No and a *positive* AMOUNT. Both fields are parsed and
cross-checked against each other (see _extract_vouchers) -- if AMOUNT's
sign is inconsistent with ISDEEMEDPOSITIVE, or a voucher's legs don't sum to
zero, that's treated as a parse error, not silently trusted. In the
debit-positive (debit - credit) convention schemas/trial_balance.py's
LedgerBalance.net_balance and schemas/tally_data.py's TallyData use, a leg's
effect on its ledger's balance is always exactly `-AMOUNT`, regardless of
which side it's on.

Balance-sheet vs P&L filtering (parse_tally_xml only)
--------------------------------------------------------
checks/opening_balance_vs_prior_year_closing.py's own docstring ("ASSUMPTION")
states it assumes every ledger in its input is a *permanent* (balance-sheet)
ledger, and would incorrectly flag a Profit & Loss ledger's "disappearance"
between years -- P&L ledgers correctly reset to zero at year-end and never
appear in a prior-year closing trial balance, that's not a discrepancy.
Real Tally ledger masters always sit under one of Tally's fixed built-in
primary groups, which cleanly separates the two: PROFIT_AND_LOSS_PARENT_GROUPS
below is exactly Tally's own list of P&L primary groups (Sales Accounts,
Purchase Accounts, Direct/Indirect Incomes, Direct/Indirect Expenses) -- not
something specific to any one company's naming choices. parse_tally_xml
excludes any ledger whose PARENT *resolves to* one of those groups (see
TallyData.resolve_top_level_group, added 2026-08-05) from the TrialBalance it
returns -- this walks any custom sub-group nesting (e.g. a company-created
"Domestic Sales" group nested under the built-in "Sales Accounts") rather
than only matching a ledger's immediate PARENT string, since real-world
exports (unlike this generator's output, which never nests groups) commonly
do nest custom groups under a standard one.

KNOWN LIMITATION, found while building checks/opening_balance_vs_prior_year_closing.py
against real Tally data (2026-08-02): that check compares a stated opening
balance against last year's closing -- a same-point-in-time continuity fact.
A TrialBalance built from a *full year* of realistic, uncorrelated trading
vouchers (via parse_tally_xml -> TallyData.closing_balance) will legitimately
differ from the opening balance for any genuinely active ledger; that's real
business activity, not a discrepancy, and running it through
opening_balance_vs_prior_year_closing.py produces false positives on nearly
every active ledger. That check should only ever be fed a Tally file's
OPENINGBALANCE-derived position (a snapshot with no vouchers, or vouchers
that net to zero for the year), never a full year of real trading data.
checks/suspense_account_scrutiny.py, which consumes TallyData/voucher detail
directly rather than going through parse_tally_xml, is the check actually
validated against data-synthesizer's Tally XML samples -- see that check's
docstring and tests/verify_suspense_account_scrutiny_against_data_synthesizer.py.

Failure modes (raises TallyXmlParseError, never silently misparses)
-----------------------------------------------------------------------
- Not well-formed XML, or the root element isn't <ENVELOPE>.
- No <LEDGER> masters found at all.
- A <LEDGER> with no NAME attribute, a missing PARENT/OPENINGBALANCE, an
  unparseable OPENINGBALANCE, or a NAME that duplicates an earlier ledger.
- A <GROUP> with no NAME attribute, or a NAME that duplicates an earlier
  group (a missing PARENT is tolerated, unlike LEDGER -- see
  _extract_group_masters).
- A <VOUCHER> with fewer than 2 ledger entries (not valid double-entry), a
  ledger entry missing LEDGERNAME/ISDEEMEDPOSITIVE/AMOUNT, an
  ISDEEMEDPOSITIVE value that isn't exactly "Yes"/"No", an AMOUNT that can't
  be parsed as a decimal or whose sign is inconsistent with ISDEEMEDPOSITIVE,
  a voucher whose legs don't sum to zero, or a leg referencing a ledger name
  with no matching <LEDGER> master.
- (parse_tally_xml only) after excluding P&L-group ledgers, nothing
  permanent is left to check.
"""
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Set

from schemas.tally_data import TallyData, TallyGroupMaster, TallyLedgerMaster, TallyVoucher, TallyVoucherLeg
from schemas.trial_balance import LedgerBalance, TrialBalance

PROFIT_AND_LOSS_PARENT_GROUPS = {
    "Sales Accounts",
    "Purchase Accounts",
    "Direct Incomes",
    "Indirect Incomes",
    "Direct Expenses",
    "Indirect Expenses",
}


class TallyXmlParseError(ValueError):
    """Raised when a file cannot be confidently parsed as this Tally XML export shape."""


def _required_text(element: ET.Element, tag: str, context: str) -> str:
    child = element.find(tag)
    if child is None or child.text is None or not child.text.strip():
        raise TallyXmlParseError(f"{context}: missing required <{tag}> element.")
    return child.text.strip()


def _parse_decimal(raw: str, context: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation as e:
        raise TallyXmlParseError(f"{context}: could not parse '{raw}' as a decimal amount.") from e


def _parse_tally_date(raw: str, context: str) -> str:
    try:
        return datetime.strptime(raw, "%Y%m%d").date().isoformat()
    except ValueError as e:
        raise TallyXmlParseError(f"{context}: could not parse '{raw}' as a Tally date (expected YYYYMMDD).") from e


def _extract_ledger_masters(root: ET.Element) -> Dict[str, TallyLedgerMaster]:
    masters: Dict[str, TallyLedgerMaster] = {}
    for ledger_el in root.iter("LEDGER"):
        name = ledger_el.get("NAME")
        if not name:
            raise TallyXmlParseError("Found a <LEDGER> element with no NAME attribute.")
        if name in masters:
            raise TallyXmlParseError(f"Duplicate ledger master '{name}' -- ambiguous which opening balance is authoritative.")

        parent = _required_text(ledger_el, "PARENT", f"Ledger '{name}'")
        opening_raw = _required_text(ledger_el, "OPENINGBALANCE", f"Ledger '{name}'")
        opening_balance = _parse_decimal(opening_raw, f"Ledger '{name}' OPENINGBALANCE")

        masters[name] = TallyLedgerMaster(name=name, parent=parent, opening_balance=opening_balance)

    if not masters:
        raise TallyXmlParseError("No <LEDGER> master elements found -- doesn't look like a Tally export.")

    return masters


def _extract_group_masters(root: ET.Element) -> Dict[str, TallyGroupMaster]:
    """Parses <GROUP> master elements into custom-group name -> parent
    entries (see TallyGroupMaster's docstring for why only custom groups
    appear here, never Tally's built-in primary groups). Unlike a LEDGER's
    PARENT, a GROUP's PARENT is not required here -- Tally itself always
    populates one for a real custom group, but treating a missing PARENT as
    a parse error would be too strict for a master element that (unlike
    LEDGER/VOUCHER) this project doesn't otherwise validate; it just becomes
    a dead end for resolve_top_level_group's walk instead.
    """
    groups: Dict[str, TallyGroupMaster] = {}
    for group_el in root.iter("GROUP"):
        name = group_el.get("NAME")
        if not name:
            raise TallyXmlParseError("Found a <GROUP> element with no NAME attribute.")
        if name in groups:
            raise TallyXmlParseError(f"Duplicate group master '{name}' -- ambiguous which parent is authoritative.")

        parent = group_el.findtext("PARENT") or ""
        groups[name] = TallyGroupMaster(name=name, parent=parent.strip())

    return groups


def _extract_vouchers(root: ET.Element, known_ledger_names: Set[str]) -> List[TallyVoucher]:
    vouchers: List[TallyVoucher] = []

    for voucher_el in root.iter("VOUCHER"):
        vn = voucher_el.findtext("VOUCHERNUMBER") or "(missing voucher number)"
        vch_type = voucher_el.get("VCHTYPE") or "(missing VCHTYPE)"
        date = _parse_tally_date(_required_text(voucher_el, "DATE", f"Voucher '{vn}'"), f"Voucher '{vn}' DATE")
        narration = voucher_el.findtext("NARRATION") or ""

        entries = voucher_el.findall("ALLLEDGERENTRIES.LIST")
        if len(entries) < 2:
            raise TallyXmlParseError(f"Voucher '{vn}' has fewer than 2 ledger entries -- not a valid double-entry voucher.")

        legs: List[TallyVoucherLeg] = []
        for entry in entries:
            ledger_name = _required_text(entry, "LEDGERNAME", f"Voucher '{vn}' ledger entry")
            if ledger_name not in known_ledger_names:
                raise TallyXmlParseError(f"Voucher '{vn}' references ledger '{ledger_name}', which has no matching <LEDGER> master.")

            deemed_raw = _required_text(entry, "ISDEEMEDPOSITIVE", f"Voucher '{vn}' entry for '{ledger_name}'")
            if deemed_raw not in ("Yes", "No"):
                raise TallyXmlParseError(
                    f"Voucher '{vn}' entry for '{ledger_name}': ISDEEMEDPOSITIVE must be 'Yes' or 'No', got '{deemed_raw}'."
                )
            is_debit = deemed_raw == "Yes"

            amount_raw = _required_text(entry, "AMOUNT", f"Voucher '{vn}' entry for '{ledger_name}'")
            amount = _parse_decimal(amount_raw, f"Voucher '{vn}' entry for '{ledger_name}' AMOUNT")

            # See module docstring "Tally's sign convention" -- reject
            # rather than silently trust one field over the other.
            if is_debit and amount > 0:
                raise TallyXmlParseError(
                    f"Voucher '{vn}' entry for '{ledger_name}': ISDEEMEDPOSITIVE=Yes (debit) but AMOUNT is "
                    f"positive ({amount}) -- expected a non-positive amount per Tally's sign convention."
                )
            if not is_debit and amount < 0:
                raise TallyXmlParseError(
                    f"Voucher '{vn}' entry for '{ledger_name}': ISDEEMEDPOSITIVE=No (credit) but AMOUNT is "
                    f"negative ({amount}) -- expected a non-negative amount per Tally's sign convention."
                )

            legs.append(TallyVoucherLeg(ledger_name=ledger_name, is_debit=is_debit, amount=amount))

        leg_sum = sum((leg.amount for leg in legs), Decimal("0"))
        if leg_sum != 0:
            raise TallyXmlParseError(f"Voucher '{vn}': ledger entries do not sum to zero (got {leg_sum}) -- not a valid double-entry voucher.")

        vouchers.append(TallyVoucher(vch_type=vch_type, voucher_number=vn, date=date, narration=narration, legs=legs))

    return vouchers


def parse_tally_xml_data(xml_bytes: bytes) -> TallyData:
    """Parses a raw Tally XML export into a TallyData -- every ledger master
    and every voucher, fully preserved. Takes raw bytes, not a decoded
    string -- the file's own XML declaration states its encoding, and
    ElementTree.fromstring rejects a str that also carries one.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise TallyXmlParseError(f"Not well-formed XML: {e}") from e

    if root.tag != "ENVELOPE":
        raise TallyXmlParseError(f"Expected root element <ENVELOPE>, found <{root.tag}> -- doesn't look like a Tally export.")

    masters = _extract_ledger_masters(root)
    groups = _extract_group_masters(root)
    vouchers = _extract_vouchers(root, set(masters))
    return TallyData(ledgers=masters, vouchers=vouchers, groups=groups)


def parse_tally_xml_data_file(path: str) -> TallyData:
    """Convenience wrapper: reads `path` as raw bytes and parses it. Raises
    OSError (e.g. FileNotFoundError) if the file can't be read, same as
    TrialBalance.from_csv."""
    return parse_tally_xml_data(Path(path).read_bytes())


def parse_tally_xml(xml_bytes: bytes) -> TrialBalance:
    """Parses a raw Tally XML export and collapses it into a TrialBalance of
    permanent (balance-sheet) ledgers' *closing* balances -- see module
    docstring "Balance-sheet vs P&L filtering" and "KNOWN LIMITATION" before
    using this against opening_balance_vs_prior_year_closing.py.
    """
    data = parse_tally_xml_data(xml_bytes)

    ledgers: List[LedgerBalance] = []
    for name, master in data.ledgers.items():
        if data.resolve_top_level_group(name) in PROFIT_AND_LOSS_PARENT_GROUPS:
            continue
        closing = data.closing_balance(name)
        debit = closing if closing >= 0 else Decimal("0.00")
        credit = -closing if closing < 0 else Decimal("0.00")
        ledgers.append(LedgerBalance(name=name, group=master.parent, debit=debit, credit=credit))

    if not ledgers:
        raise TallyXmlParseError("No permanent (balance-sheet) ledgers found after excluding Profit & Loss groups -- nothing to check.")

    ledgers.sort(key=lambda l: l.name)
    return TrialBalance(ledgers=ledgers)


def parse_tally_xml_file(path: str) -> TrialBalance:
    """Convenience wrapper: reads `path` as raw bytes and parses it. Raises
    OSError (e.g. FileNotFoundError) if the file can't be read, same as
    TrialBalance.from_csv."""
    return parse_tally_xml(Path(path).read_bytes())
