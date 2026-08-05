"""Turns a raw file-parsing exception into a plain-language, user-facing
error -- a category, a one-line message, a suggested next step, and the
original technical error preserved separately for anyone who wants it.

Extracted out of api.py (added 2026-08-08) so this logic stays testable via
the fast, stdlib-only `unittest discover` suite -- api.py itself requires
FastAPI (this project's one deliberate stdlib exception, see CLAUDE.md
"Conventions"), but the classification logic underneath it doesn't need
FastAPI at all, it's just isinstance checks against this project's own
parse-error exception types. api.py imports classify_parse_error and wires
its result into an HTTPException; this module has no HTTP awareness at all.

Why this exists: a raw parser exception message (e.g. "Not well-formed XML:
no element found: line 563384, column 7") is meaningless to a non-technical
user uploading a file. Every category below is something a user can
actually act on; only "unknown" means "this is a bug in our code, not a
problem with your file" (see is_file_problem).
"""
from __future__ import annotations

from typing import NamedTuple

from tally_xml_parser import (
    TallyXmlEncodingError,
    TallyXmlMalformedError,
    TallyXmlNotATallyExportError,
    TallyXmlParseError,
    TallyXmlTruncatedError,
)
from trial_balance_csv_parser import TrialBalanceParseError


class ClassifiedParseError(NamedTuple):
    """`status_code` is the HTTP status api.py should respond with -- 422
    for every named category (a problem WITH THE FILE) and 500 only for
    "unknown" (a problem with THIS PROJECT'S code, not the file)."""
    status_code: int
    category: str
    is_file_problem: bool
    message: str
    suggested_fix: str
    technical_detail: str

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "is_file_problem": self.is_file_problem,
            "message": self.message,
            "suggested_fix": self.suggested_fix,
            "technical_detail": self.technical_detail,
        }


def classify_parse_error(e: Exception) -> ClassifiedParseError:
    if isinstance(e, TallyXmlTruncatedError):
        return ClassifiedParseError(
            status_code=422,
            category="file_truncated",
            is_file_problem=True,
            message="This file appears to be incomplete — it stops partway through and is missing its proper ending.",
            suggested_fix="This usually happens if the export was interrupted or the file was cut off during download/transfer. Re-export this file from Tally and try uploading it again.",
            technical_detail=str(e),
        )
    if isinstance(e, (TallyXmlEncodingError, UnicodeDecodeError)):
        return ClassifiedParseError(
            status_code=422,
            category="unsupported_encoding",
            is_file_problem=True,
            message="This file's text encoding could not be recognized.",
            suggested_fix="We support UTF-8 and UTF-16 files (Tally's usual export formats). If this file was opened and re-saved by another program, re-export it directly from Tally and try again.",
            technical_detail=str(e),
        )
    if isinstance(e, TallyXmlNotATallyExportError):
        return ClassifiedParseError(
            status_code=422,
            category="not_a_tally_export",
            is_file_problem=True,
            message="This file doesn't look like a Tally export — it's missing the ledger/voucher structure we expect.",
            suggested_fix="Confirm this is a genuine Tally XML export (not a renamed file of a different type), and re-export if unsure.",
            technical_detail=str(e),
        )
    if isinstance(e, TallyXmlMalformedError):
        return ClassifiedParseError(
            status_code=422,
            category="not_valid_xml",
            is_file_problem=True,
            message="This file doesn't appear to be in the expected XML format.",
            suggested_fix="Confirm this is a genuine Tally XML export (not a renamed file of a different type), and re-export if unsure.",
            technical_detail=str(e),
        )
    if isinstance(e, (TallyXmlParseError, TrialBalanceParseError)):
        # Base class / CSV parse error -- a recognized problem with the
        # file's DATA that doesn't fit one of the more specific categories
        # above (e.g. a duplicate ledger, a voucher whose legs don't sum to
        # zero, a CSV missing a required column). The existing exception
        # message is already written to be reasonably clear on its own (see
        # tally_xml_parser.py / trial_balance_csv_parser.py), so it doubles
        # as both `message` and `technical_detail` here.
        message = str(e)
        return ClassifiedParseError(
            status_code=422,
            category="file_data_issue",
            is_file_problem=True,
            message=message,
            suggested_fix="Please review this file's data for accuracy, correct the issue, and try uploading again. If you believe this file is correct, contact support with the technical details below.",
            technical_detail=message,
        )
    # Genuine catch-all: not any recognized parse-error type at all, so this
    # is an actual bug in this project's own code, not a problem with the
    # user's file.
    return ClassifiedParseError(
        status_code=500,
        category="unknown",
        is_file_problem=False,
        message="Something went wrong on our end trying to process this file — this isn't a problem with your file.",
        suggested_fix="Please note the exact error below and share it with support / the development team.",
        technical_detail=f"{type(e).__name__}: {e}",
    )
