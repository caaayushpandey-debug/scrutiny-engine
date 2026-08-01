"""Coordinator: given what's been uploaded for a client/FY and a specific
version being scrutinized, determines which registered checks (see
checks/registry.py) CAN run now and which CANNOT yet -- and exactly which
requirement(s) are missing for the ones that can't.

This module only answers "is the data there yet" -- it does not load
documents or execute checks. That's a deliberate boundary: readiness
(coordinator.py) and execution (each check's own run_check/
run_check_from_files) are separate concerns, and there's no real document
store to load from yet anyway.

Scope resolution rules (CLAUDE.md HARD RULE #7 "Document scope model"):
- version_scoped requirements are resolved against the SPECIFIC version
  being scrutinized -- a document uploaded for a different version of the
  same FY does not satisfy it.
- period_scoped_external and period_scoped_prior_year requirements are
  resolved against the FY as a whole, independent of which version is being
  scrutinized -- these documents are uploaded once and reused across every
  version.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

from checks.registry import CHECK_REGISTRY, CheckDefinition
from checks.requirements import DataRequirement
from schemas.enums import DocumentScope, DocumentType


@dataclass
class AvailableDocuments:
    """What's been uploaded for one client + FY, as of a point in time.

    There's no real document store yet -- this is the shape the coordinator
    needs, decoupled from how uploads are actually persisted. Construct one
    of these (or adapt this shape) from whatever the real store says is
    uploaded once that exists.
    """
    client_id: str
    fy: str
    # period_scoped_external / period_scoped_prior_year docs: uploaded once
    # for the FY, independent of version. Keyed by (document_type, scope)
    # rather than just document_type because scope still matters for
    # matching a requirement exactly -- e.g. nothing stops a future document
    # type from legitimately having both a period_scoped_external and a
    # period_scoped_prior_year variant, the way TrialBalance does today
    # across version_scoped/period_scoped_prior_year.
    period_scoped_documents: Set[Tuple[DocumentType, DocumentScope]] = field(default_factory=set)
    # version_scoped docs: uploaded per version. version_id -> set of
    # document types uploaded for that specific version.
    version_scoped_documents_by_version: Dict[str, Set[DocumentType]] = field(default_factory=dict)

    def add_period_scoped(self, document_type: DocumentType, scope: DocumentScope) -> None:
        self.period_scoped_documents.add((document_type, scope))

    def add_version_scoped(self, version_id: str, document_type: DocumentType) -> None:
        self.version_scoped_documents_by_version.setdefault(version_id, set()).add(document_type)

    def has(self, requirement: DataRequirement, version_id: str) -> bool:
        if requirement.scope == DocumentScope.VERSION_SCOPED:
            return requirement.document_type in self.version_scoped_documents_by_version.get(version_id, set())
        return (requirement.document_type, requirement.scope) in self.period_scoped_documents


@dataclass
class CheckReadiness:
    check_id: str
    name: str
    can_run: bool
    missing: List[DataRequirement]

    def missing_descriptions(self) -> List[str]:
        return [r.description for r in self.missing]


def evaluate_check_readiness(
    check_def: CheckDefinition,
    available: AvailableDocuments,
    version_id: str,
) -> CheckReadiness:
    missing = [req for req in check_def.requirements if not available.has(req, version_id)]
    return CheckReadiness(
        check_id=check_def.check_id,
        name=check_def.name,
        can_run=(len(missing) == 0),
        missing=missing,
    )


def evaluate_all_checks(
    available: AvailableDocuments,
    version_id: str,
    registry: List[CheckDefinition] = CHECK_REGISTRY,
) -> List[CheckReadiness]:
    return [evaluate_check_readiness(check_def, available, version_id) for check_def in registry]
