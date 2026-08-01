"""PLACEHOLDER schema for GSTR-2 (inward supplies / auto-drafted ITC, i.e.
GSTR-2B in current GST practice).

Not yet defined -- flesh this out when the first check that consumes GSTR-2
is built. Scope: DocumentScope.PERIOD_SCOPED_EXTERNAL (see schemas/enums.py
DEFAULT_SCOPE_BY_DOCUMENT_TYPE).
"""
from dataclasses import dataclass


@dataclass
class GSTR2:
    pass
