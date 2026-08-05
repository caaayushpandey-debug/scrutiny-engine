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
    """A company-created CUSTOM group, e.g. "Overseas Debtors" nested under
    the built-in "Sundry Debtors". Tally only emits a <GROUP> master for a
    group a company actually created -- its own ~28 reserved primary groups
    (Sundry Debtors, Capital Account, Sales Accounts, etc.) never get one,
    since they're built in, not something a company defines. That's exactly
    what lets TallyData.resolve_top_level_group (below) know when to stop
    walking: once `parent` (or a ledger's own `parent`) names something that
    isn't a key in `TallyData.groups`, it's reached a primary group (or a
    group whose own PARENT was never supplied)."""
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
        """Walks this ledger's group ancestry upward through any custom
        sub-groups (via `groups`) until it reaches a name that isn't itself a
        custom group -- i.e. one of Tally's built-in reserved primary groups,
        or a custom group whose own PARENT was never supplied. Without this,
        a ledger filed under a custom sub-group (e.g. "Overseas Debtors"
        under "Sundry Debtors") would be classified by its own immediate
        PARENT string ("Overseas Debtors"), which will never match any of
        Tally's fixed primary-group names.

        `max_depth` guards against a cyclical PARENT chain, which would mean
        corrupt data (Tally itself doesn't allow creating one), not a real
        export -- rather than looping forever, this just stops and returns
        wherever the walk got to.
        """
        current = self.ledgers[ledger_name].parent
        seen = {current}
        depth = 0
        while current in self.groups and depth < max_depth:
            nxt = self.groups[current].parent
            if nxt in seen:
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
