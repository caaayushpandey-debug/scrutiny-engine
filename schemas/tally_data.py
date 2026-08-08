"""TallyData document schema -- ledger masters plus a year of vouchers.

Matches the XML shape produced by the `data-synthesizer` project's Tally XML
generator (`generators/tally_xml/generate_tally_xml.py`) and parsed by
`tally_xml_parser.py` at the top level of this project. This is the
canonical definition of "the current year's Tally data" as a document type:
checks that need voucher-level detail (not just an aggregated opening/
closing trial balance figure) import from here, the same way
`schemas/trial_balance.py` is the canonical shape for checks that only need
a single stated balance per ledger.

Unlike TrialBalance (one number per ledger), TallyData keeps the full
ledger-master + voucher-leg detail, because a ledger's *closing* balance
here isn't a single stored field -- see `closing_balance()`, which computes
it as opening balance plus the signed effect of every voucher leg
referencing that ledger. The debit-positive (debit - credit) sign
convention matches `LedgerBalance.net_balance` in schemas/trial_balance.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List


@dataclass
class TallyLedgerMaster:
    name: str
    parent: str
    opening_balance: Decimal


@dataclass
class TallyGroupMaster:
    """A group master parsed from a <GROUP> element -- typically a
    company-created CUSTOM group (e.g. "Overseas Debtors" nested under the
    built-in "Sundry Debtors").

    CORRECTION (2026-08-08, against real client files): the original
    assumption here -- that Tally emits a <GROUP> master ONLY for
    company-created groups, never for its own built-in primary groups -- is
    FALSE for real exports. Real Tally files routinely emit <GROUP> masters
    for the built-in primaries themselves ("Sundry Creditors" with PARENT
    "Current Liabilities", "Sales Accounts"/"Current Liabilities"/etc. with an
    EMPTY parent). So `TallyData.groups` can contain built-in primaries too,
    and resolve_top_level_group can no longer assume "not a key in groups"
    means "reached a primary". It now stops at the last non-empty group in the
    chain instead (see below). It also tolerates the same NAME appearing twice
    (real files duplicate group masters, with identical parent) -- dedup is
    handled by the parser, not here."""
    name: str
    parent: str


@dataclass
class TallyVoucherLeg:
    ledger_name: str
    is_debit: bool
    amount: Decimal  # Tally's own signed AMOUNT field, as parsed -- not a magnitude


@dataclass
class TallyVoucher:
    vch_type: str
    voucher_number: str
    date: str  # ISO YYYY-MM-DD
    narration: str
    legs: List[TallyVoucherLeg]

    def leg_for(self, ledger_name: str) -> TallyVoucherLeg:
        return next(leg for leg in self.legs if leg.ledger_name == ledger_name)

    def touches(self, ledger_name: str) -> bool:
        return any(leg.ledger_name == ledger_name for leg in self.legs)


@dataclass
class TallyData:
    ledgers: Dict[str, TallyLedgerMaster]
    vouchers: List[TallyVoucher]
    # Custom group masters only (see TallyGroupMaster) -- defaults to empty
    # since most real exports (and every sample this project has been
    # validated against so far) never nest custom groups at all, and older
    # callers that construct a TallyData directly (e.g. api.py's
    # TallyDataIn.to_domain, which chains from a prior /parse-tally-xml
    # response) may not supply one.
    groups: Dict[str, TallyGroupMaster] = field(default_factory=dict)

    def resolve_top_level_group(self, ledger_name: str, max_depth: int = 20) -> str:
        """Walks this ledger's group ancestry upward through `groups` and
        returns the top-level group name -- the last group in the chain whose
        own PARENT is empty/absent (a primary group), or the last one before
        the chain leaves `groups` entirely. Without this, a ledger filed under
        a custom sub-group (e.g. "Overseas Debtors" under "Sundry Debtors")
        would be classified by its own immediate PARENT string ("Overseas
        Debtors"), which never matches any of Tally's fixed primary-group
        names.

        Two real-world cases this must handle (the second added 2026-08-08):
        1. The built-in primary is NOT emitted as a group master (synthetic
           data, and some real exports): the chain steps out of `groups` when
           it names the primary; we return that name.
        2. The built-in primary IS emitted as a group master (common in real
           exports -- see TallyGroupMaster's corrected docstring), e.g.
           "Food & Grocery Vendors" -> "Sundry Creditors" -> "Current
           Liabilities", where "Current Liabilities" has an EMPTY parent. Here
           we must STOP AT "Current Liabilities" and return it -- NOT step
           into its empty parent "" and return "". Returning "" would break
           P&L filtering (a "Sales Accounts"-parented ledger would resolve to
           "" instead of "Sales Accounts" and wrongly survive the P&L
           exclusion).

        `max_depth` guards against a cyclical PARENT chain (corrupt data, not
        a real export) -- it stops and returns wherever the walk got to.
        """
        current = self.ledgers[ledger_name].parent
        seen = {current}
        depth = 0
        while current in self.groups and depth < max_depth:
            nxt = self.groups[current].parent
            # Stop at `current` (the last real group) rather than stepping
            # into an empty/absent parent or revisiting a seen node. This is
            # what keeps a primary group emitted as a master (empty PARENT)
            # from resolving to "" -- see case 2 above.
            if not nxt or nxt in seen:
                break
            current = nxt
            seen.add(current)
            depth += 1
        return current

    def closing_balance(self, ledger_name: str) -> Decimal:
        """opening balance + the signed, debit-positive effect of every
        voucher leg touching this ledger. A leg's debit-positive effect is
        always `-amount`: a debit leg's negative AMOUNT and a credit leg's
        positive AMOUNT both negate to the correct sign under Tally's
        convention (see tally_xml_parser.py's module docstring)."""
        total = self.ledgers[ledger_name].opening_balance
        for voucher in self.vouchers:
            for leg in voucher.legs:
                if leg.ledger_name == ledger_name:
                    total -= leg.amount
        return total

    def vouchers_touching(self, ledger_name: str) -> List[TallyVoucher]:
        return [v for v in self.vouchers if v.touches(ledger_name)]
