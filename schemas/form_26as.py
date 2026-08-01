"""PLACEHOLDER schema for Form 26AS (TDS/TCS credit statement).

Not yet defined -- flesh this out when the first check that consumes Form
26AS is built. Scope: DocumentScope.PERIOD_SCOPED_EXTERNAL (see
schemas/enums.py DEFAULT_SCOPE_BY_DOCUMENT_TYPE).
"""
from dataclasses import dataclass


@dataclass
class Form26AS:
    pass
