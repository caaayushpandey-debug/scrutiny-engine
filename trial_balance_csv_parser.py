"""Tolerant CSV parser for Trial Balance files, converting real-world CSV
variations into the canonical TrialBalance shape (schemas/trial_balance.py).

Reference format: data-synthesizer's generator output --
`Ledger Name,Group,Debit,Credit` -- see
../data-synthesizer/generators/trial_balance/generate_trial_balance.py and
its samples/trial_balance/*/*.csv. This parser accepts that exact format plus
reasonable real-world variations (different header names/casing/order,
currency symbols, thousands separators, parenthesized negatives). It does
NOT support a fundamentally different layout, e.g. a single signed "Amount"
column instead of separate Debit/Credit columns -- that's a different,
explicitly-declared input shape, not a "variation" of this one, and silently
guessing which sign convention it uses would be exactly the kind of silent
misparse this module exists to avoid.

Header matching
----------------
Column headers are matched by alias, not position -- order doesn't matter.
Matching is case/whitespace/punctuation-insensitive (see _normalize_header),
so "Ledger Name", "ledger_name", "LEDGER-NAME" all match the same alias.

Ledger Name, Debit, and Credit are required -- the check cannot run without
them, so a file missing any of these is rejected outright. Group is optional
(defaults to "" if no matching column is found): it's descriptive metadata
only, never used in the check's actual comparison logic
(LedgerBalance.net_balance / opening_balance_vs_prior_year_closing.py's
ledger-name matching), so relaxing it doesn't risk silently corrupting a real
financial comparison the way relaxing Debit/Credit would.

Number tolerance
-----------------
Before Decimal() conversion, strips: currency symbols (Rs., Rs, INR, Rupee
sign), thousands-separator commas, surrounding whitespace. Converts
accounting-style parenthesized negatives ("(1,234.56)") to a leading minus.
An empty cell is treated as 0.00 -- a ledger row with only a debit and no
credit commonly has a blank credit cell in real exports, not an explicit
"0.00".

Failure modes (raises TrialBalanceParseError, never silently misparses)
--------------------------------------------------------------------
- File has no header row, or a header row but zero data rows.
- No column found for Ledger Name, Debit, or Credit -- the error message
  lists the aliases considered and the actual headers found, so the caller
  can tell whether the file is genuinely not a trial balance or just needs
  another alias added.
- A row's Ledger Name is empty, or its Debit/Credit can't be coerced to a
  number even after the cleanup above.
- A ledger name appears more than once in the file (ambiguous which balance
  is authoritative) -- same rule schemas/trial_balance.py's strict from_csv
  already enforces for the canonical format; kept consistent here rather
  than silently picking one occurrence.
"""
from __future__ import annotations

import csv
import io
import re
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Set

from schemas.trial_balance import LedgerBalance, TrialBalance


class TrialBalanceParseError(ValueError):
    """Raised when a CSV cannot be confidently parsed as a trial balance."""


NAME_ALIASES = {"ledger name", "ledger", "particulars", "account name", "account", "name"}
GROUP_ALIASES = {"group", "account group", "ledger group", "head", "category"}
DEBIT_ALIASES = {"debit", "dr", "debit amount", "debit amt", "debit inr", "dr amount"}
CREDIT_ALIASES = {"credit", "cr", "credit amount", "credit amt", "credit inr", "cr amount"}

# Rs. / Rs / INR / the rupee sign, optionally followed by a period -- stripped
# before numeric parsing. Compiled with IGNORECASE so "rs.", "RS", "Rs" all match.
_CURRENCY_JUNK = re.compile(r"(rs\.?|inr|₹)", re.IGNORECASE)


def _normalize_header(header: str) -> str:
    """Lowercase, strip, collapse whitespace/punctuation to single spaces --
    so "Ledger Name", "ledger_name", "LEDGER-NAME" all normalize the same."""
    cleaned = re.sub(r"[^a-z0-9]+", " ", header.strip().lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _find_column(
    fieldnames: List[str],
    normalized_to_original: Dict[str, str],
    aliases: Set[str],
    field_label: str,
    required: bool,
) -> Optional[str]:
    for normalized, original in normalized_to_original.items():
        if normalized in aliases:
            return original
    if required:
        raise TrialBalanceParseError(
            f"Could not find a '{field_label}' column. Looked for headers matching one "
            f"of: {sorted(aliases)}. Actual columns found: {fieldnames}."
        )
    return None


def _clean_decimal(raw: str, field_label: str, ledger_name: str, row_num: int) -> Decimal:
    text = raw.strip()
    if text == "":
        return Decimal("0.00")

    text = _CURRENCY_JUNK.sub("", text).strip()
    text = text.replace(",", "")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]

    try:
        return Decimal(text)
    except InvalidOperation as e:
        raise TrialBalanceParseError(
            f"Row {row_num}: could not parse '{field_label}' value '{raw}' for ledger "
            f"'{ledger_name}' as a number."
        ) from e


def parse_trial_balance_csv(text: str) -> TrialBalance:
    """Parses already-decoded CSV text into a TrialBalance, tolerant of
    reasonable real-world header/formatting variations. See module docstring
    for exactly what's tolerated and what raises TrialBalanceParseError.
    """
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise TrialBalanceParseError("File has no header row -- is this an empty file?")

    normalized_to_original: Dict[str, str] = {}
    for h in reader.fieldnames:
        normalized_to_original.setdefault(_normalize_header(h), h)

    name_col = _find_column(reader.fieldnames, normalized_to_original, NAME_ALIASES, "Ledger Name", required=True)
    debit_col = _find_column(reader.fieldnames, normalized_to_original, DEBIT_ALIASES, "Debit", required=True)
    credit_col = _find_column(reader.fieldnames, normalized_to_original, CREDIT_ALIASES, "Credit", required=True)
    group_col = _find_column(reader.fieldnames, normalized_to_original, GROUP_ALIASES, "Group", required=False)

    ledgers: List[LedgerBalance] = []
    seen_names = set()
    row_count = 0

    for line_num, row in enumerate(reader, start=2):  # header is line 1
        row_count += 1
        name = (row.get(name_col) or "").strip()
        if not name:
            raise TrialBalanceParseError(f"Row {line_num}: empty Ledger Name.")
        if name in seen_names:
            raise TrialBalanceParseError(
                f"Row {line_num}: duplicate ledger name '{name}' -- ambiguous which "
                "balance is authoritative, refusing to guess."
            )
        seen_names.add(name)

        group = (row.get(group_col) or "").strip() if group_col else ""
        debit = _clean_decimal(row.get(debit_col) or "", "Debit", name, line_num)
        credit = _clean_decimal(row.get(credit_col) or "", "Credit", name, line_num)

        ledgers.append(LedgerBalance(name=name, group=group, debit=debit, credit=credit))

    if row_count == 0:
        raise TrialBalanceParseError("File has a header row but no data rows -- nothing to parse.")

    return TrialBalance(ledgers=ledgers)
