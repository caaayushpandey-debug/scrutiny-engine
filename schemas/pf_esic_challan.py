"""PLACEHOLDER schema for PF & ESIC Challans.

Not yet defined -- flesh this out when the first check that consumes
PF/ESIC challan data is built. Scope: DocumentScope.PERIOD_SCOPED_EXTERNAL
(see schemas/enums.py DEFAULT_SCOPE_BY_DOCUMENT_TYPE).
"""
from dataclasses import dataclass


@dataclass
class PFESICChallan:
    pass
