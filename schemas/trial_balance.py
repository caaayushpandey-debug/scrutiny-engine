"""TrialBalance document schema -- fully defined.

Matches the CSV shape produced by the `data-synthesizer` project's trial
balance generator (`Ledger Name,Group,Debit,Credit`) and what
checks/opening_balance_vs_prior_year_closing.py consumes. This is the
canonical definition; that check (and any future check needing a trial
balance) imports LedgerBalance/TrialBalance from here rather than defining
its own copy.

TrialBalance is the one document type used in two different scope roles --
see schemas/enums.py's DEFAULT_SCOPE_BY_DOCUMENT_TYPE docstring for why it
has no single default scope. A given check's DataRequirement states which
scope (DocumentScope.VERSION_SCOPED for the current period, or
DocumentScope.PERIOD_SCOPED_PRIOR_YEAR for last year's closing balance) it
means for that particular requirement.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import List


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
class TrialBalance:
    ledgers: List[LedgerBalance]

    @classmethod
    def from_csv(cls, path: str) -> "TrialBalance":
        """Parses a trial balance CSV in the `Ledger Name,Group,Debit,Credit`
        shape. Raises ValueError on a malformed row or a duplicate ledger
        name within the file (ambiguous which balance is authoritative), and
        OSError (e.g. FileNotFoundError) if the file can't be read.
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

        return cls(ledgers=rows)
