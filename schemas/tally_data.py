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

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List


@dataclass
class TallyLedgerMaster:
    name: str
    parent: str
    opening_balance: Decimal


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
