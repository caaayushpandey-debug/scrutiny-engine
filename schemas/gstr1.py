"""PLACEHOLDER schema for GSTR-1 (outward supplies return).

Not yet defined -- flesh this out (fields, loader from whatever raw format
GSTR-1 data arrives in) when the first check that consumes GSTR-1 is built.
Scope: DocumentScope.PERIOD_SCOPED_EXTERNAL (see schemas/enums.py
DEFAULT_SCOPE_BY_DOCUMENT_TYPE).
"""
from dataclasses import dataclass


@dataclass
class GSTR1:
    pass
