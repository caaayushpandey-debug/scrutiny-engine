"""Shared "what does a check need" model.

Every check declares a module-level DATA_REQUIREMENTS list of these instead
of hardcoding file paths -- the check registry (checks/registry.py) collects
these, and the coordinator (coordinator.py) uses them to determine, for a
given client/FY/version, which checks can run yet and which are missing
input.
"""
from dataclasses import dataclass

from schemas.enums import DocumentScope, DocumentType


@dataclass(frozen=True)
class DataRequirement:
    # How the check itself refers to this input internally (its function
    # parameter name / role), e.g. "prior_year_trial_balance". Not just a
    # label -- the coordinator's readiness report uses this to say exactly
    # which requirement is missing.
    role: str
    document_type: DocumentType
    scope: DocumentScope
    # Human-readable, used in "missing X" messages.
    description: str
