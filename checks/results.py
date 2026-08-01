"""Shared check output shape (CLAUDE.md HARD RULE #2 and HARD RULE #6).

Every check module should import CheckResult/SourceReference from here
rather than redefining them, so the output contract lives in exactly one
place -- the same "don't reinvent it per check" principle as the schemas
package on the input side.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Optional


@dataclass
class SourceReference:
    ledger: Optional[str] = None
    voucher_number: Optional[str] = None
    date: Optional[str] = None


@dataclass
class CheckResult:
    check_id: str
    status: str  # "pass" | "flagged" | "insufficient_data"
    confidence_score: float
    description: str
    amount: Optional[Decimal]
    source_reference: SourceReference = field(default_factory=SourceReference)
    # HARD RULE #6: populated only when status == "flagged"; None otherwise.
    finding: Optional[str] = None
    potential_implication: Optional[str] = None
    recommended_manual_check: Optional[str] = None
    why_correction_matters: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["amount"] = float(self.amount) if self.amount is not None else None
        return d
