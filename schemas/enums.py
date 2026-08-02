"""Shared enums for the document schema layer.

DocumentScope describes WHEN/HOW OFTEN a document type is uploaded relative
to a client's financial year and its versions, and therefore how the
coordinator (see coordinator.py) must resolve "has this document been
provided" for a given check's requirement. See CLAUDE.md's "Document scope
model" (HARD RULE #7) for the full rationale -- this module is just the
enum, not the policy explanation.
"""
from enum import Enum


class DocumentScope(str, Enum):
    # Tied to a specific client + FY + version. Changes with every new
    # upload (e.g. this version's Trial Balance / Tally data).
    VERSION_SCOPED = "version_scoped"
    # Tied to a client + FY, uploaded once, reused across every version of
    # that FY (e.g. GSTR-1, GSTR-2, GSTR-3B, TDS Return, Form 26AS,
    # PF/ESIC Challan, Bank Statement, Payroll Report).
    PERIOD_SCOPED_EXTERNAL = "period_scoped_external"
    # Tied to a client + FY, uploaded once at FY setup (before V1 exists),
    # reused across every version -- specifically last year's audited
    # closing Trial Balance, which does not change as current-year versions
    # are revised.
    PERIOD_SCOPED_PRIOR_YEAR = "period_scoped_prior_year"


class DocumentType(str, Enum):
    TRIAL_BALANCE = "trial_balance"
    TALLY_DATA = "tally_data"
    GSTR1 = "gstr1"
    GSTR2 = "gstr2"
    GSTR3B = "gstr3b"
    TDS_RETURN = "tds_return"
    FORM_26AS = "form_26as"
    PF_ESIC_CHALLAN = "pf_esic_challan"
    BANK_STATEMENT = "bank_statement"
    PAYROLL_REPORT = "payroll_report"


# Canonical scope for document types that only ever appear in exactly one
# scope. TRIAL_BALANCE is deliberately excluded from this map: it's the one
# document type used in two different scope roles depending on context --
# this year's version-scoped upload vs last year's period_scoped_prior_year
# closing balance. Every check that needs a TrialBalance must state which
# scope it means for that specific requirement (see checks/requirements.py
# DataRequirement); there is no single sensible default for it, so looking
# it up here would silently paper over a real ambiguity. TALLY_DATA has no
# such ambiguity -- unlike TRIAL_BALANCE, no check needs "last year's Tally
# data" as a distinct role, only the current version's, so it's safe to
# default here.
DEFAULT_SCOPE_BY_DOCUMENT_TYPE = {
    DocumentType.TALLY_DATA: DocumentScope.VERSION_SCOPED,
    DocumentType.GSTR1: DocumentScope.PERIOD_SCOPED_EXTERNAL,
    DocumentType.GSTR2: DocumentScope.PERIOD_SCOPED_EXTERNAL,
    DocumentType.GSTR3B: DocumentScope.PERIOD_SCOPED_EXTERNAL,
    DocumentType.TDS_RETURN: DocumentScope.PERIOD_SCOPED_EXTERNAL,
    DocumentType.FORM_26AS: DocumentScope.PERIOD_SCOPED_EXTERNAL,
    DocumentType.PF_ESIC_CHALLAN: DocumentScope.PERIOD_SCOPED_EXTERNAL,
    DocumentType.BANK_STATEMENT: DocumentScope.PERIOD_SCOPED_EXTERNAL,
    DocumentType.PAYROLL_REPORT: DocumentScope.PERIOD_SCOPED_EXTERNAL,
}
