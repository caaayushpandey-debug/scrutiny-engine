"""PLACEHOLDER schema for GSTR-3B (summary return).

Not yet defined -- flesh this out when the first check that consumes
GSTR-3B is built. Scope: DocumentScope.PERIOD_SCOPED_EXTERNAL (see
schemas/enums.py DEFAULT_SCOPE_BY_DOCUMENT_TYPE).
"""
from dataclasses import dataclass


@dataclass
class GSTR3B:
    pass
