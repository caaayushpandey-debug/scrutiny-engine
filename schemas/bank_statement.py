"""PLACEHOLDER schema for Bank Statement.

Not yet defined -- flesh this out when the first check that consumes a bank
statement is built. Scope: DocumentScope.PERIOD_SCOPED_EXTERNAL (see
schemas/enums.py DEFAULT_SCOPE_BY_DOCUMENT_TYPE).
"""
from dataclasses import dataclass


@dataclass
class BankStatement:
    pass
