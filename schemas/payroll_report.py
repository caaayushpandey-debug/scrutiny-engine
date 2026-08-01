"""PLACEHOLDER schema for Payroll Report.

Not yet defined -- flesh this out when the first check that consumes a
payroll report is built. Scope: DocumentScope.PERIOD_SCOPED_EXTERNAL (see
schemas/enums.py DEFAULT_SCOPE_BY_DOCUMENT_TYPE).
"""
from dataclasses import dataclass


@dataclass
class PayrollReport:
    pass
