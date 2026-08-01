"""PLACEHOLDER schema for TDS Return (Form 24Q / 26Q).

Not yet defined -- flesh this out when the first check that consumes a TDS
Return is built. Scope: DocumentScope.PERIOD_SCOPED_EXTERNAL (see
schemas/enums.py DEFAULT_SCOPE_BY_DOCUMENT_TYPE).
"""
from dataclasses import dataclass


@dataclass
class TDSReturn:
    pass
