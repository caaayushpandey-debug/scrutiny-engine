"""Tally XML -> TallyData / TrialBalance parser.

Reference schema: the same Tally Import/Export XML shape documented in
`data-synthesizer`'s Tally XML generator (see that project's
generators/tally_xml/generate_tally_xml.py docstring for the exact citation
of Tally's own Developer Reference and public sample exports this is based
on) -- ENVELOPE > HEADER/BODY > IMPORTDATA > REQUESTDESC/REQUESTDATA >
TALLYMESSAGE, each containing a LEDGER master, a GROUP master, or a VOUCHER.
LEDGER uses NAME (attribute), PARENT, OPENINGBALANCE. GROUP uses NAME
(attribute) and PARENT -- Tally only emits a GROUP master for a
company-created custom group (its own built-in reserved primary groups never
get one; see schemas/tally_data.py's TallyGroupMaster and
TallyData.resolve_top_level_group). VOUCHER uses VCHTYPE (attribute),
VOUCHERNUMBER, DATE, NARRATION, and one ALLLEDGERENTRIES.LIST per leg
(LEDGERNAME, ISDEEMEDPOSITIVE, AMOUNT).

Two entry points for a single, self-contained file
---------------------------------------------------
- `parse_tally_xml_data` returns a `schemas.tally_data.TallyData` -- ledger
  masters plus every voucher, fully preserved (voucher number, date,
  narration, every leg). Checks that need to reason about *which specific
  voucher* touched a ledger (e.g. checks/suspense_account_scrutiny.py) need
  this level of detail, not just a final number.
- `parse_tally_xml` collapses that into a `schemas.trial_balance.TrialBalance`
  (one net balance per permanent ledger) -- see "Balance-sheet vs P&L
  filtering" below. This exists for checks that only need a stated balance
  per ledger, the same shape trial_balance_csv_parser.py produces.

A third entry point for a real-world SPLIT export
----------------------------------------------------
`parse_tally_xml_data_multi(files: List[bytes])` -- confirmed against real
user files (2026-08-06), Tally commonly exports a company's masters
(<GROUP>/<LEDGER>) and transactions (<VOUCHER>) as two SEPARATE files rather
than one combined file. Pass both (in either order) and it merges them into
one TallyData, the same shape parse_tally_xml_data returns. See "Split
masters/transactions exports" further down for the full explanation,
including why <REPORTNAME> is never trusted to tell the two apart.

Encoding
--------
Real exports are commonly UTF-16 with a byte-order mark, not UTF-8 (also
confirmed against real user files, 2026-08-06) -- every entry point takes
raw bytes (never a decoded str) and sniffs the encoding itself via
_normalize_xml_encoding, which also strips characters XML 1.0 doesn't allow
at all before handing anything to ElementTree -- both as a numeric entity
reference (observed 2026-08-06, e.g. &#4;) and, distinctly, as a raw
character embedded literally in the decoded text (observed 2026-08-07,
inside a <STATKEY> field -- see _strip_raw_illegal_control_chars). Neither
form is meaningful accounting data; both are dropped entirely.

Why computing a closing balance is harder than reading a CSV cell
----------------------------------------------------------------------
trial_balance_csv_parser.py reads a single stated number per ledger. Tally
XML only states each ledger's OPENING balance directly -- a *closing*
balance (TallyData.closing_balance) isn't stored anywhere as a single
field; it's computed as opening balance plus the signed effect of every
voucher leg referencing that ledger, anywhere in the file.

Tally's sign convention (verified against the generator's own output, and
against real sample exports it's modelled on): a debit leg USUALLY has
ISDEEMEDPOSITIVE=Yes and a *negative* AMOUNT; a credit leg USUALLY has
ISDEEMEDPOSITIVE=No and a *positive* AMOUNT. Both fields are parsed, but a
disagreement between them is NO LONGER treated as an error (relaxed
2026-08-08): real files legitimately disagree on adjustment legs -- a real
"Rounding Off" leg was marked ISDEEMEDPOSITIVE=No (credit) yet carried a
negative AMOUNT (-0.20). The signed AMOUNT is authoritative for all balance
math (a leg's effect on its ledger's balance is always exactly `-AMOUNT`, in
the debit-positive convention schemas/trial_balance.py's
LedgerBalance.net_balance and schemas/tally_data.py's TallyData use,
regardless of which side ISDEEMEDPOSITIVE claims), and the real integrity
guard is that a voucher's legs must still sum to zero (that check is kept and
still raises). ISDEEMEDPOSITIVE must still be exactly "Yes"/"No" (a garbage
value is still rejected); it just no longer has to agree with AMOUNT's sign.

Balance-sheet vs P&L filtering (parse_tally_xml only)
--------------------------------------------------------
checks/opening_balance_vs_prior_year_closing.py's own docstring ("ASSUMPTION")
states it assumes every ledger in its input is a *permanent* (balance-sheet)
ledger, and would incorrectly flag a Profit & Loss ledger's "disappearance"
between years -- P&L ledgers correctly reset to zero at year-end and never
appear in a prior-year closing trial balance, that's not a discrepancy.
Real Tally ledger masters always sit under one of Tally's fixed built-in
primary groups, which cleanly separates the two: PROFIT_AND_LOSS_PARENT_GROUPS
below is exactly Tally's own list of P&L primary groups (Sales Accounts,
Purchase Accounts, Direct/Indirect Incomes, Direct/Indirect Expenses) -- not
something specific to any one company's naming choices. parse_tally_xml
excludes any ledger whose PARENT *resolves to* one of those groups (see
TallyData.resolve_top_level_group, added 2026-08-05) from the TrialBalance it
returns -- this walks any custom sub-group nesting (e.g. a company-created
"Domestic Sales" group nested under the built-in "Sales Accounts") rather
than only matching a ledger's immediate PARENT string, since real-world
exports (unlike this generator's output, which never nests groups) commonly
do nest custom groups under a standard one.

KNOWN LIMITATION, found while building checks/opening_balance_vs_prior_year_closing.py
against real Tally data (2026-08-02): that check compares a stated opening
balance against last year's closing -- a same-point-in-time continuity fact.
A TrialBalance built from a *full year* of realistic, uncorrelated trading
vouchers (via parse_tally_xml -> TallyData.closing_balance) will legitimately
differ from the opening balance for any genuinely active ledger; that's real
business activity, not a discrepancy, and running it through
opening_balance_vs_prior_year_closing.py produces false positives on nearly
every active ledger. That check should only ever be fed a Tally file's
OPENINGBALANCE-derived position (a snapshot with no vouchers, or vouchers
that net to zero for the year), never a full year of real trading data.
checks/suspense_account_scrutiny.py, which consumes TallyData/voucher detail
directly rather than going through parse_tally_xml, is the check actually
validated against data-synthesizer's Tally XML samples -- see that check's
docstring and tests/verify_suspense_account_scrutiny_against_data_synthesizer.py.

Failure modes (raises a TallyXmlParseError subclass, never silently misparses)
-------------------------------------------------------------------------------
TallyXmlParseError itself has a small subclass hierarchy (added 2026-08-08)
so callers -- specifically api.py's error classification, which turns each
into a plain-language, user-facing message -- can tell a handful of common,
specifically-named problems apart from each other and from everything else,
without parsing error message text:
- TallyXmlEncodingError -- cannot decode a file as UTF-8 or UTF-16 (checked
  via byte-order mark) at all; fails before any XML parsing is attempted.
- TallyXmlTruncatedError -- the file's XML structure never reaches a
  complete state (expat ran out of input mid-document, or the file is
  empty) -- distinguished from other malformed XML via expat's own error
  code, not by guessing from the message text. Confirmed against a real
  client file (2026-08-08) that was genuinely cut off mid-transfer.
- TallyXmlMalformedError -- not well-formed XML for any other reason (a bad
  token, mismatched tags, junk after the root element, etc).
- TallyXmlNotATallyExportError -- well-formed XML, but the root element
  isn't <ENVELOPE>, or there's no <LEDGER> master anywhere in it (or, for
  parse_tally_xml_data_multi, in ANY of the supplied files).
- TallyXmlParseError (the base class, raised directly) -- everything else:
  a <LEDGER>/<GROUP> with no NAME attribute or a duplicate NAME, a <LEDGER>
  with no <PARENT> element AT ALL (an EMPTY <PARENT/> is legitimate --
  Tally's own reserved ledgers, e.g. "Profit & Loss A/c", have no parent
  group at all -- see _required_element_optional_text), a present-but-
  unparseable OPENINGBALANCE (an ABSENT <OPENINGBALANCE> is legitimate --
  the same "Profit & Loss A/c" ledger also omits it entirely for a
  genuinely zero opening balance -- see _optional_decimal_element, which
  defaults to 0.00 rather than erroring), a <VOUCHER> with fewer than 2
  ledger entries, a ledger entry missing LEDGERNAME/ISDEEMEDPOSITIVE/AMOUNT
  or with an ISDEEMEDPOSITIVE that isn't exactly "Yes"/"No", an AMOUNT that
  can't be parsed as a decimal or whose sign is inconsistent with
  ISDEEMEDPOSITIVE, a voucher whose legs don't sum to
  zero, a leg referencing a ledger name with no matching <LEDGER> master
  (for parse_tally_xml_data_multi, only once no fragment anywhere in the
  upload has a matching master), duplicate ledger/group names across
  multiple files in parse_tally_xml_data_multi, or (parse_tally_xml only)
  nothing permanent left to check after excluding P&L-group ledgers. These
  are all recognized problems with the FILE's data, just not ones specific
  enough to name their own subclass for.
"""
import codecs
import re
import xml.etree.ElementTree as ET
import xml.parsers.expat
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional, Set

from schemas.tally_data import TallyData, TallyGroupMaster, TallyLedgerMaster, TallyVoucher, TallyVoucherLeg
from schemas.trial_balance import LedgerBalance, TrialBalance

PROFIT_AND_LOSS_PARENT_GROUPS = {
    "Sales Accounts",
    "Purchase Accounts",
    "Direct Incomes",
    "Indirect Incomes",
    "Direct Expenses",
    "Indirect Expenses",
}


class TallyXmlParseError(ValueError):
    """Raised when a file cannot be confidently parsed as this Tally XML export
    shape. This is also the category for a recognized problem with the
    FILE's data/content that doesn't fit one of the more specific subclasses
    below (e.g. a duplicate ledger, a voucher whose legs don't sum to zero,
    ISDEEMEDPOSITIVE/AMOUNT sign mismatch) -- callers (see api.py's error
    classification) treat "TallyXmlParseError raised directly, not as one of
    its named subclasses" as its own "something about this file's DATA looks
    wrong" bucket, distinct from the structural problems the subclasses
    below name specifically. All of these still mean "the FILE has a
    problem", never "our code has a bug" -- see the subclass docstrings
    below for what to raise instead when the problem is more specific, and
    api.py for the one place that turns each of these into a plain-language,
    user-facing message.
    """


class TallyXmlEncodingError(TallyXmlParseError):
    """The file's bytes could not be decoded as UTF-8 or UTF-16 at all --
    raised by _normalize_xml_encoding when every encoding this module knows
    how to sniff (via BOM, or a bare UTF-8 fallback) fails to decode the raw
    bytes. Distinct from TallyXmlMalformedError: this fails before ANY XML
    parsing is even attempted."""


class TallyXmlTruncatedError(TallyXmlParseError):
    """The file's XML structure never reaches a complete, well-formed state
    -- expat ran out of input (end-of-file) while still expecting more
    content, rather than encountering an actual malformed token partway
    through. Confirmed against a real client file (2026-08-08): a large
    Transactions.xml that was cut off mid-transfer/mid-export raised exactly
    this ("no element found" at a line/column deep into the file, not at the
    start) -- see _parse_envelope_root for how this is detected (expat's own
    error code, xml.parsers.expat.errors.codes["no element found"], not
    string-matching the message text)."""


class TallyXmlMalformedError(TallyXmlParseError):
    """The file is not well-formed XML, for a reason OTHER than running out
    of input (see TallyXmlTruncatedError for that specific case) -- e.g. a
    genuinely invalid token, mismatched tags, or content after the root
    element closes. Raised by _parse_envelope_root."""


class TallyXmlNotATallyExportError(TallyXmlParseError):
    """The file decodes and parses as well-formed XML, but doesn't have the
    structure a Tally export is expected to have -- either the root element
    isn't <ENVELOPE>, or there isn't a single <LEDGER> master anywhere in
    it. Raised by _parse_envelope_root and _extract_ledger_masters /
    merge_tally_xml_fragments respectively."""


# Valid XML 1.0 character ranges (spec section 2.2) -- everything else is
# not well-formed XML at all, even as a numeric character reference. Real
# Tally exports have been observed (2026-08-06, against real user files)
# containing references like &#4; (a C0 control character, "End of
# Transmission") that violate this -- Tally itself doesn't seem to mind
# emitting them, but expat (which ElementTree uses) rejects them outright as
# not well-formed. See _strip_invalid_numeric_entities below.
def _is_valid_xml_char(codepoint: int) -> bool:
    return (
        codepoint in (0x9, 0xA, 0xD)
        or 0x20 <= codepoint <= 0xD7FF
        or 0xE000 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0x10FFFF
    )


_NUMERIC_ENTITY_RE = re.compile(r"&#(x?)([0-9a-fA-F]+);")
_XML_DECLARATION_RE = re.compile(rb"^\s*<\?xml[^>]*\?>\s*")

# Precomputed once at import: every BMP codepoint XML 1.0 doesn't allow
# literally in content, mapped to None (str.translate deletes it). Only the
# BMP (0x0-0xFFFF) needs entries -- every codepoint from 0x10000-0x10FFFF is
# valid per _is_valid_xml_char, so translate() leaves those untouched
# automatically (an unmapped codepoint passes through as-is). Built from
# _is_valid_xml_char itself so this can never drift out of sync with the one
# definition of "valid" both stripping functions below rely on.
_ILLEGAL_XML_CHAR_TRANSLATION = {cp: None for cp in range(0x0, 0x10000) if not _is_valid_xml_char(cp)}


def _strip_invalid_numeric_entities(text: str) -> str:
    """Removes numeric character references (&#4; or &#x4;) that point at a
    codepoint XML 1.0 doesn't allow at all -- these aren't meaningful
    accounting data, just an artifact of whatever produced the export, so
    dropping the reference entirely is equivalent to the stray control
    character never having been there. A *valid* numeric reference (e.g.
    &#8377; for the Rupee sign) is left untouched.

    This only handles the &#N; ENTITY spelling of an illegal character --
    see _strip_raw_illegal_control_chars for the same problem when the
    illegal character is embedded literally, not as an entity reference.
    """
    def _replace(m: "re.Match[str]") -> str:
        codepoint = int(m.group(2), 16 if m.group(1) else 10)
        return m.group(0) if _is_valid_xml_char(codepoint) else ""
    return _NUMERIC_ENTITY_RE.sub(_replace, text)


def _strip_raw_illegal_control_chars(text: str) -> str:
    """Removes characters embedded LITERALLY in decoded text that XML 1.0
    does not allow in content at all -- a distinct problem from
    _strip_invalid_numeric_entities above, which only catches the &#N;
    entity-reference spelling. Confirmed against real user files
    (2026-08-07): a <STATKEY> field contained a raw ASCII 0x05 (ENQ) control
    character written directly into the text, not spelled out as &#5; --
    e.g. "2023\\x05376\\x05Outward Invoice\\x05S1.4.2023", where 0x05 looks
    to be Tally's own internal delimiter joining several values into one
    field. expat rejects a literal illegal character exactly as it rejects
    the equivalent entity reference, regardless of which form produced it,
    so this covers the full XML 1.0 illegal range (via _is_valid_xml_char /
    _ILLEGAL_XML_CHAR_TRANSLATION) rather than just the one control
    character observed so far -- nothing guarantees 0x05 is the only one
    Tally ever emits raw.

    Applied to the WHOLE document text, not just STATKEY: encoding
    normalization runs before any per-element parsing, so at this point
    there is no way to know which tag a given character sits inside, and an
    illegal literal character anywhere breaks well-formedness for the
    entire file, not just for its own field -- scoping this to one tag
    isn't possible at this stage even if it were desirable. STATKEY's
    content isn't consumed by any check this project runs today, so
    stripping delimiter characters out of it is harmless -- it was already
    effectively discarded either way. CONFIRMED CLEAN against real client
    data (2026-08-07): every occurrence of this delimiter pattern in the
    real ~61MB Transactions.xml this was found in was checked, and it
    appears ONLY in STATKEY -- NARRATION, LEDGERNAME, PARTYNAME, and
    VOUCHERNUMBER are all clean of it. Not a structural guarantee (a
    different real export could still differ, in which case stripping the
    delimiter would silently concatenate that field's joined sub-values
    with no separator, losing real information rather than discarding
    noise) -- but confirmed clean against the one real file this was found
    in.
    """
    return text.translate(_ILLEGAL_XML_CHAR_TRANSLATION)


def _normalize_xml_encoding(xml_bytes: bytes) -> bytes:
    """Real Tally exports are commonly UTF-16 with a BOM (confirmed against
    real user files, 2026-08-06) rather than the UTF-8 every sample this
    project had previously been tested against uses. Sniffs the BOM (UTF-16
    LE/BE, or UTF-8) to decode correctly, strips any illegal-per-XML-1.0
    characters -- both the &#N; entity-reference form (see
    _strip_invalid_numeric_entities) and the same characters embedded
    literally/raw in the text (see _strip_raw_illegal_control_chars, added
    2026-08-07 after real user files turned up a raw control character
    Tally itself doesn't seem to mind emitting but expat rejects outright)
    -- and re-encodes everything to plain UTF-8 bytes with no leading BOM,
    so the rest of this module, and ElementTree/expat, only ever have to
    deal with one encoding.

    Re-encoding to UTF-8 ourselves (rather than relying on
    ElementTree/expat's own BOM autodetection) also sidesteps a specific
    hazard: the original file's <?xml ... encoding="UTF-16"?> declaration
    would still be sitting at the top of the text after we've decoded it,
    and now genuinely describes the WRONG encoding for the UTF-8 bytes we're
    about to hand back -- expat trusts that declaration over the actual
    bytes and misparses (or errors) as a result. Stripping the XML
    declaration entirely avoids that: content with no declaration at all
    defaults to UTF-8 per the XML spec, which is exactly what this function
    just produced.
    """
    if xml_bytes.startswith(codecs.BOM_UTF16_LE):
        text = xml_bytes[len(codecs.BOM_UTF16_LE):].decode("utf-16-le")
    elif xml_bytes.startswith(codecs.BOM_UTF16_BE):
        text = xml_bytes[len(codecs.BOM_UTF16_BE):].decode("utf-16-be")
    elif xml_bytes.startswith(codecs.BOM_UTF8):
        text = xml_bytes[len(codecs.BOM_UTF8):].decode("utf-8")
    else:
        try:
            text = xml_bytes.decode("utf-8")
        except UnicodeDecodeError as e:
            raise TallyXmlEncodingError(
                f"Could not decode file as UTF-8 or UTF-16 (checked for a byte-order mark of either): {e}"
            ) from e

    text = _strip_invalid_numeric_entities(text)
    text = _strip_raw_illegal_control_chars(text)
    normalized = text.encode("utf-8")
    return _XML_DECLARATION_RE.sub(b"", normalized, count=1)


# expat error codes that specifically mean "ran out of input before the
# document's XML structure was complete" -- i.e. truncation (or a genuinely
# empty file, which hits the same code). Determined empirically, not just
# from the one real reported case: cutting a realistic multi-KB document at
# 200 random points and observing which codes actually came back showed
# BOTH "no element found" (parser reached EOF with no/incomplete root) AND
# "unclosed token" (EOF while a start tag/attribute was still open) --
# roughly 3 unclosed-token results for every no-element-found one, so
# matching only the exact code from the one real report seen so far
# (XML_ERROR_NO_ELEMENTS) would have missed most other truncation points.
# XML_ERROR_UNCLOSED_CDATA_SECTION and XML_ERROR_PARTIAL_CHAR are the same
# category of problem (EOF mid-token) even though neither came up in that
# survey -- Tally XML doesn't appear to use CDATA sections, and
# _normalize_xml_encoding always re-encodes to complete, valid UTF-8 before
# handing bytes to expat, so a partial multi-byte character shouldn't
# realistically reach expat either -- included for completeness/robustness,
# not because either has been observed.
_TRUNCATION_EXPAT_CODES = frozenset(
    xml.parsers.expat.errors.codes[name]
    for name in (
        "no element found",
        "unclosed token",
        "unclosed CDATA section",
        "partial character",
    )
)


def _parse_envelope_root(xml_bytes: bytes) -> ET.Element:
    """Normalizes encoding, parses the XML, and validates the root element
    is <ENVELOPE> -- shared by parse_tally_xml_data (single, self-contained
    file) and parse_tally_xml_fragment (one piece of a split export, see
    "Split masters/transactions exports" below).
    """
    normalized = _normalize_xml_encoding(xml_bytes)
    try:
        root = ET.fromstring(normalized)
    except ET.ParseError as e:
        # expat's own error code -- not string-matching e's message text --
        # distinguishes "ran out of input before the document was complete"
        # (see _TRUNCATION_EXPAT_CODES: both a truncated/cut-off file and a
        # genuinely empty one hit one of these) from every other kind of
        # malformed XML (a genuinely bad token mid-document, a mismatched
        # tag, junk after the root closes, etc). Confirmed against a real
        # client file (2026-08-08): a large Transactions.xml cut off
        # mid-transfer raised exactly "no element found", at a line/column
        # deep into the file, not at the start.
        if e.code in _TRUNCATION_EXPAT_CODES:
            raise TallyXmlTruncatedError(f"File appears incomplete (ran out of content before the XML structure was complete): {e}") from e
        raise TallyXmlMalformedError(f"Not well-formed XML: {e}") from e

    if root.tag != "ENVELOPE":
        raise TallyXmlNotATallyExportError(f"Expected root element <ENVELOPE>, found <{root.tag}> -- doesn't look like a Tally export.")

    return root


def _required_text(element: ET.Element, tag: str, context: str) -> str:
    child = element.find(tag)
    if child is None or child.text is None or not child.text.strip():
        raise TallyXmlParseError(f"{context}: missing required <{tag}> element.")
    return child.text.strip()


def _required_element_optional_text(element: ET.Element, tag: str, context: str) -> str:
    """Like _required_text, but an EMPTY or self-closing element (e.g.
    <PARENT/>) is a legitimate value, not an error -- returns "" in that
    case rather than raising. The element itself must still be present;
    only a completely ABSENT element still raises.

    Confirmed against real client data (2026-08-09): Tally's own reserved
    top-level ledgers -- "Profit & Loss A/c" is the confirmed real example --
    genuinely have no parent group at all, and a real export represents
    that as an empty <PARENT/> tag, not by omitting the tag. Requiring
    non-empty text (what _required_text does) rejected this as "missing
    required <PARENT> element", which was wrong: the field wasn't missing,
    it was legitimately empty.

    Why the element itself is still required (judgment call, not
    independently verified against a live Tally installation): no real
    export seen so far has ever omitted the <PARENT> tag itself, only left
    it empty. UPDATE (2026-08-10): the broader claim this reasoning
    originally rested on -- "Tally always emits every known field's tag,
    populated or not, never omitting one outright" -- turned out to be
    wrong in general: <OPENINGBALANCE> IS omitted entirely by real Tally
    exports for a zero balance (see _extract_ledger_masters_raw's handling
    of it, added right after this was discovered). So Tally's real
    behavior is field-specific, not a single blanket rule -- PARENT being
    empty-but-present and OPENINGBALANCE being omitted-when-zero are two
    different serialization choices for two different fields, not the same
    pattern applied inconsistently. A <LEDGER> with no <PARENT> tag AT ALL
    still hasn't been observed in any real file, so it still fails loud
    here rather than silently guessing "" -- but this specific judgment
    call should be revisited (the same way OPENINGBALANCE's was) the
    moment a real file actually shows it.
    """
    child = element.find(tag)
    if child is None:
        raise TallyXmlParseError(f"{context}: missing required <{tag}> element.")
    return (child.text or "").strip()


def _parse_decimal(raw: str, context: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation as e:
        raise TallyXmlParseError(f"{context}: could not parse '{raw}' as a decimal amount.") from e


def _optional_decimal_element(element: ET.Element, tag: str, context: str, default: Decimal) -> Decimal:
    """Like _required_text + _parse_decimal combined, but a completely
    ABSENT element is treated as `default` rather than an error. If the
    element IS present, it must still contain a valid, non-empty decimal --
    this only changes what happens when the tag is missing outright, not
    when it's present but malformed.

    Confirmed against real client data (2026-08-10): Tally omits
    <OPENINGBALANCE> entirely for a ledger whose opening balance is exactly
    zero, rather than emitting <OPENINGBALANCE>0.00</OPENINGBALANCE> --
    real example: the same reserved "Profit & Loss A/c" ledger from the
    <PARENT/> fix (2026-08-09), which turned out to omit OPENINGBALANCE too,
    just via a different mechanism (omitting the tag, not emitting it
    empty -- see _required_element_optional_text's docstring for the
    now-corrected assumption this contradicts). This is a general Tally
    behavior, not specific to that one ledger -- applied here to every
    <LEDGER>, since the same omission could plausibly appear on any ledger
    with a genuinely zero opening balance, not just reserved ones.
    """
    child = element.find(tag)
    if child is None:
        return default
    if child.text is None or not child.text.strip():
        raise TallyXmlParseError(f"{context}: missing required <{tag}> element.")
    return _parse_decimal(child.text.strip(), f"{context} {tag}")


def _parse_tally_date(raw: str, context: str) -> str:
    try:
        return datetime.strptime(raw, "%Y%m%d").date().isoformat()
    except ValueError as e:
        raise TallyXmlParseError(f"{context}: could not parse '{raw}' as a Tally date (expected YYYYMMDD).") from e


def _reconcile_group(name: str, existing: TallyGroupMaster, new: TallyGroupMaster, strict: bool = True) -> TallyGroupMaster:
    """Combine two group masters sharing a NAME (real files duplicate group
    masters -- both within one file and across a masters+transactions pair --
    see "Real-world structural robustness pass" in CLAUDE.md). Identical is a
    no-op; a missing/empty PARENT defers to the populated one.

    `strict` controls what happens on a genuine conflict (two DIFFERENT
    non-empty parents): within a SINGLE file (strict=True) that's corruption
    and raises; ACROSS files (strict=False, see merge_tally_xml_fragments)
    it's tolerated and `existing` wins -- merge orders the dedicated masters
    file first, so its value is the one kept."""
    if existing.parent == new.parent:
        return existing
    if not existing.parent:
        return new
    if not new.parent:
        return existing
    if strict:
        raise TallyXmlParseError(
            f"Group master '{name}' appears more than once with conflicting parents "
            f"('{existing.parent}' vs '{new.parent}') -- ambiguous which is authoritative."
        )
    return existing


def _reconcile_ledger(name: str, existing: TallyLedgerMaster, new: TallyLedgerMaster, strict: bool = True) -> TallyLedgerMaster:
    """Combine two ledger masters sharing a NAME. Real files carry the same
    ledger in both the masters file and the (self-contained) transactions
    file, often with one stating an <OPENINGBALANCE> the other omits, OR with
    two DIFFERENT non-zero balances because the files are snapshots as-of
    different dates. Rule: prefer the populated PARENT and the non-zero
    opening balance (an omitted OPENINGBALANCE defaults to 0.00, so a real
    stated balance always wins over an omitted one).

    On a genuine conflict -- two different non-empty parents, or two different
    NON-ZERO opening balances -- `strict` decides: within a SINGLE file
    (strict=True) it's corruption and raises; ACROSS files (strict=False) it's
    a legitimate snapshot difference (confirmed real: a bank ledger had
    different opening balances in the masters file vs the transactions file)
    and `existing` wins. merge_tally_xml_fragments orders the dedicated
    masters file (most ledger masters) FIRST, so its opening balance -- the
    authoritative one for opening_balance_vs_prior_year_closing -- is kept."""
    if existing.parent == new.parent:
        parent = existing.parent
    elif not existing.parent:
        parent = new.parent
    elif not new.parent:
        parent = existing.parent
    elif strict:
        raise TallyXmlParseError(
            f"Ledger master '{name}' appears more than once with conflicting parents "
            f"('{existing.parent}' vs '{new.parent}')."
        )
    else:
        parent = existing.parent

    if existing.opening_balance == new.opening_balance:
        opening = existing.opening_balance
    elif existing.opening_balance == 0:
        opening = new.opening_balance
    elif new.opening_balance == 0:
        opening = existing.opening_balance
    elif strict:
        raise TallyXmlParseError(
            f"Ledger master '{name}' appears more than once with conflicting opening balances "
            f"({existing.opening_balance} vs {new.opening_balance}) -- ambiguous which is authoritative."
        )
    else:
        opening = existing.opening_balance
    return TallyLedgerMaster(name=name, parent=parent, opening_balance=opening)


def _extract_ledger_masters_raw(root: ET.Element) -> Dict[str, TallyLedgerMaster]:
    """The actual extraction, with no requirement that any <LEDGER> be
    present -- a transactions-only fragment of a split export (see "Split
    masters/transactions exports" below) legitimately has none.
    _extract_ledger_masters wraps this with that requirement for the
    single-combined-file case.
    """
    masters: Dict[str, TallyLedgerMaster] = {}
    for ledger_el in root.iter("LEDGER"):
        # Strip the NAME attribute: real Tally pads some ledger names with a
        # trailing space in the master's NAME attribute (e.g. NAME="Ashok
        # Joshi ") while the SAME ledger's <LEDGERNAME> references in vouchers
        # are unpadded ("Ashok Joshi"). _required_text already strips
        # <LEDGERNAME>/<PARENT>, so the master NAME must be stripped too or the
        # ledger never matches its own legs (found against real files
        # 2026-08-08 -- previously raised "references ledger '...' which has no
        # matching <LEDGER> master").
        name = (ledger_el.get("NAME") or "").strip()
        if not name:
            raise TallyXmlParseError("Found a <LEDGER> element with no NAME attribute.")

        # PARENT may legitimately be EMPTY (<PARENT/>) -- see
        # _required_element_optional_text's docstring for the confirmed
        # real-world case (Tally's reserved "Profit & Loss A/c" ledger has
        # no parent group at all). OPENINGBALANCE may legitimately be
        # ABSENT entirely -- a different omission mechanism for a different
        # field, see _optional_decimal_element's docstring -- defaulting to
        # a genuinely zero balance rather than erroring. If OPENINGBALANCE
        # IS present, it must still be a valid, non-empty number; only its
        # complete absence is treated as zero.
        parent = _required_element_optional_text(ledger_el, "PARENT", f"Ledger '{name}'")
        opening_balance = _optional_decimal_element(
            ledger_el, "OPENINGBALANCE", f"Ledger '{name}'", default=Decimal("0.00")
        )

        candidate = TallyLedgerMaster(name=name, parent=parent, opening_balance=opening_balance)
        # A NAME can appear more than once even within one file (real files do
        # this); reconcile identical/complementary copies, raise on a genuine
        # conflict -- see _reconcile_ledger.
        masters[name] = _reconcile_ledger(name, masters[name], candidate) if name in masters else candidate

    return masters


def _extract_ledger_masters(root: ET.Element) -> Dict[str, TallyLedgerMaster]:
    masters = _extract_ledger_masters_raw(root)
    if not masters:
        raise TallyXmlNotATallyExportError("No <LEDGER> master elements found -- doesn't look like a Tally export.")
    return masters


def _extract_group_masters(root: ET.Element) -> Dict[str, TallyGroupMaster]:
    """Parses <GROUP> master elements into custom-group name -> parent
    entries (see TallyGroupMaster's docstring for why only custom groups
    appear here, never Tally's built-in primary groups). Unlike a LEDGER's
    PARENT, a GROUP's PARENT is not required here -- Tally itself always
    populates one for a real custom group, but treating a missing PARENT as
    a parse error would be too strict for a master element that (unlike
    LEDGER/VOUCHER) this project doesn't otherwise validate; it just becomes
    a dead end for resolve_top_level_group's walk instead.
    """
    groups: Dict[str, TallyGroupMaster] = {}
    for group_el in root.iter("GROUP"):
        # Strip NAME for the same reason as ledgers (see
        # _extract_ledger_masters_raw) -- a group NAME padded with whitespace
        # must still match a ledger's/child-group's stripped <PARENT> text.
        name = (group_el.get("NAME") or "").strip()
        if not name:
            raise TallyXmlParseError("Found a <GROUP> element with no NAME attribute.")

        parent = group_el.findtext("PARENT") or ""
        candidate = TallyGroupMaster(name=name, parent=parent.strip())
        # Real files duplicate group masters (e.g. every primary group twice,
        # identical parent), and also emit masters for built-in primaries --
        # reconcile identical/complementary copies, raise on a genuine
        # conflict. See _reconcile_group and TallyGroupMaster's docstring.
        groups[name] = _reconcile_group(name, groups[name], candidate) if name in groups else candidate

    return groups


def _voucher_is_skippable(voucher_el: ET.Element) -> bool:
    """A voucher flagged cancelled, deleted, or optional is not a posted book
    entry, so it's excluded from the parsed dataset (found against real files
    2026-08-08 -- optional vouchers appeared; cancelled/deleted are the same
    category and skipped defensively). Checked BEFORE the >=2-legs / sum-to-
    zero validation, since a cancelled voucher can legitimately be empty or
    unbalanced -- validating it would reject the whole file over an entry that
    doesn't affect the books at all."""
    return (
        (voucher_el.findtext("ISCANCELLED") or "").strip() == "Yes"
        or (voucher_el.findtext("ISDELETED") or "").strip() == "Yes"
        or (voucher_el.findtext("ISOPTIONAL") or "").strip() == "Yes"
    )


def _extract_vouchers(root: ET.Element, known_ledger_names: Optional[Set[str]]) -> List[TallyVoucher]:
    """`known_ledger_names=None` skips the "references a ledger with no
    matching master" check entirely -- used when parsing one fragment of a
    split export (see parse_tally_xml_fragment), where a voucher's ledger
    master may legitimately live in a DIFFERENT file not parsed yet. That
    check is instead performed once, correctly, against the complete merged
    ledger set by merge_tally_xml_fragments.
    """
    vouchers: List[TallyVoucher] = []

    for voucher_el in root.iter("VOUCHER"):
        if _voucher_is_skippable(voucher_el):
            continue

        vn = voucher_el.findtext("VOUCHERNUMBER") or "(missing voucher number)"
        vch_type = voucher_el.get("VCHTYPE") or "(missing VCHTYPE)"
        date = _parse_tally_date(_required_text(voucher_el, "DATE", f"Voucher '{vn}'"), f"Voucher '{vn}' DATE")
        narration = voucher_el.findtext("NARRATION") or ""

        # Two valid ledger-entry containers (found against real files
        # 2026-08-08): accounting-mode vouchers (Receipt/Payment/Contra/
        # Journal) use ALLLEDGERENTRIES.LIST; invoice-mode vouchers
        # (Purchase/Sales) use LEDGERENTRIES.LIST (with a parallel
        # ALLINVENTORYENTRIES.LIST this parser doesn't need -- the ledger
        # entries already sum to zero on their own). Read BOTH so either mode
        # parses. findall is direct-child-only, so a nested allocation list's
        # own *ENTRIES.LIST are never mistaken for top-level legs.
        entries = voucher_el.findall("ALLLEDGERENTRIES.LIST") + voucher_el.findall("LEDGERENTRIES.LIST")
        if len(entries) < 2:
            raise TallyXmlParseError(f"Voucher '{vn}' has fewer than 2 ledger entries -- not a valid double-entry voucher.")

        legs: List[TallyVoucherLeg] = []
        for entry in entries:
            ledger_name = _required_text(entry, "LEDGERNAME", f"Voucher '{vn}' ledger entry")
            if known_ledger_names is not None and ledger_name not in known_ledger_names:
                raise TallyXmlParseError(f"Voucher '{vn}' references ledger '{ledger_name}', which has no matching <LEDGER> master.")

            deemed_raw = _required_text(entry, "ISDEEMEDPOSITIVE", f"Voucher '{vn}' entry for '{ledger_name}'")
            if deemed_raw not in ("Yes", "No"):
                raise TallyXmlParseError(
                    f"Voucher '{vn}' entry for '{ledger_name}': ISDEEMEDPOSITIVE must be 'Yes' or 'No', got '{deemed_raw}'."
                )
            is_debit = deemed_raw == "Yes"

            amount_raw = _required_text(entry, "AMOUNT", f"Voucher '{vn}' entry for '{ledger_name}'")
            amount = _parse_decimal(amount_raw, f"Voucher '{vn}' entry for '{ledger_name}' AMOUNT")

            # ISDEEMEDPOSITIVE and AMOUNT's sign USUALLY agree under Tally's
            # convention (debit -> non-positive AMOUNT, credit -> non-negative),
            # but real files legitimately disagree on adjustment legs -- a real
            # "Rounding Off" entry marked ISDEEMEDPOSITIVE=No (credit) carried a
            # NEGATIVE AMOUNT (-0.20) (found 2026-08-08). The signed AMOUNT is
            # the authoritative value for all balance math (closing_balance uses
            # -amount, never is_debit), and the per-voucher sum-to-zero check
            # below is the real integrity guard, so a sign disagreement is NOT
            # rejected -- we keep both fields exactly as stated. (Previously
            # this raised and rejected the whole file over such an entry.)

            legs.append(TallyVoucherLeg(ledger_name=ledger_name, is_debit=is_debit, amount=amount))

        leg_sum = sum((leg.amount for leg in legs), Decimal("0"))
        if leg_sum != 0:
            raise TallyXmlParseError(f"Voucher '{vn}': ledger entries do not sum to zero (got {leg_sum}) -- not a valid double-entry voucher.")

        vouchers.append(TallyVoucher(vch_type=vch_type, voucher_number=vn, date=date, narration=narration, legs=legs))

    return vouchers


def parse_tally_xml_data(xml_bytes: bytes) -> TallyData:
    """Parses a raw Tally XML export into a TallyData -- every ledger master
    and every voucher, fully preserved. Takes raw bytes, not a decoded
    string -- see _normalize_xml_encoding (real exports are commonly UTF-16
    with a BOM, not UTF-8; ElementTree also rejects a str that still carries
    its own encoding declaration). Requires the file to be a single,
    self-contained export with both masters and vouchers (or masters alone)
    -- for a real-world export split across separate masters-only and
    transactions-only files, use parse_tally_xml_data_multi instead (see
    "Split masters/transactions exports" below).
    """
    root = _parse_envelope_root(xml_bytes)
    masters = _extract_ledger_masters(root)
    groups = _extract_group_masters(root)
    vouchers = _extract_vouchers(root, set(masters))
    return TallyData(ledgers=masters, vouchers=vouchers, groups=groups)


def parse_tally_xml_data_file(path: str) -> TallyData:
    """Convenience wrapper: reads `path` as raw bytes and parses it. Raises
    OSError (e.g. FileNotFoundError) if the file can't be read, same as
    TrialBalance.from_csv."""
    return parse_tally_xml_data(Path(path).read_bytes())


# ---------------------------------------------------------------------------
# Split masters/transactions exports
# ---------------------------------------------------------------------------
# Confirmed against real user files (2026-08-06): Tally commonly exports a
# company's data as two SEPARATE files -- one containing only <GROUP>/
# <LEDGER> masters, another containing only <VOUCHER> entries -- rather than
# one combined file. Both files' <REPORTNAME> can carry the same value
# regardless of which kind of content is actually inside, so it is never
# consulted here (or anywhere in this module) to decide anything; every
# function below determines what a file contains purely by which elements
# are actually present.
def parse_tally_xml_fragment(xml_bytes: bytes) -> TallyData:
    """Parses ONE file that may be only part of a split export: a
    masters-only file (<GROUP>/<LEDGER>, no <VOUCHER> at all), a
    transactions-only file (<VOUCHER> only, no <LEDGER>/<GROUP> at all), or
    a single combined file with both -- content-driven, not decided from
    <REPORTNAME> or any other label.

    Unlike parse_tally_xml_data, this does NOT require at least one <LEDGER>
    to be present (a transactions-only fragment legitimately has none), and
    does NOT validate that a voucher leg's ledger has a matching master
    WITHIN THIS FILE -- that master may live in a sibling fragment not
    parsed yet. Both checks are instead performed once, correctly, by
    merge_tally_xml_fragments below, against the complete merged dataset.

    Not meant to be used standalone for a single all-in-one file where you
    want those checks enforced immediately -- use parse_tally_xml_data for
    that.
    """
    root = _parse_envelope_root(xml_bytes)
    masters = _extract_ledger_masters_raw(root)
    groups = _extract_group_masters(root)
    vouchers = _extract_vouchers(root, known_ledger_names=None)
    return TallyData(ledgers=masters, vouchers=vouchers, groups=groups)


def merge_tally_xml_fragments(fragments: List[TallyData]) -> TallyData:
    """Combines multiple TallyData fragments (see parse_tally_xml_fragment)
    into one complete dataset, performing the same integrity checks
    parse_tally_xml_data does for a single combined file, but now correctly
    against the FULL merged picture: a voucher leg referencing a ledger
    defined in a sibling fragment is exactly the normal case for a split
    export, not an error -- it would only have been wrongly flagged if
    validated one fragment at a time (which is exactly why
    parse_tally_xml_fragment defers this check to here).

    A ledger/group master appearing in MORE THAN ONE fragment is also normal,
    not an error (found against real files 2026-08-08): a real transactions
    file embeds its own copy of the masters, overlapping the dedicated masters
    file -- often with one file stating an opening balance the other omits.
    Duplicates are reconciled (identical/complementary copies merged, prefer
    the populated PARENT and non-zero opening balance) rather than rejected;
    only a genuine conflict raises. See _reconcile_ledger / _reconcile_group.
    """
    merged_ledgers: Dict[str, TallyLedgerMaster] = {}
    merged_groups: Dict[str, TallyGroupMaster] = {}
    merged_vouchers: List[TallyVoucher] = []

    # Process the most masters-heavy fragment FIRST (stable sort) so, on a
    # cross-file opening-balance/parent conflict, the dedicated masters file's
    # value is the `existing` one _reconcile_* keeps -- that's the
    # authoritative opening balance for opening_balance_vs_prior_year_closing.
    # Cross-file reconciliation is non-strict: a snapshot difference between a
    # masters file and a transactions file is legitimate, not corruption.
    ordered = sorted(fragments, key=lambda f: len(f.ledgers), reverse=True)
    for fragment in ordered:
        for name, master in fragment.ledgers.items():
            merged_ledgers[name] = (
                _reconcile_ledger(name, merged_ledgers[name], master, strict=False) if name in merged_ledgers else master
            )

        for name, group in fragment.groups.items():
            merged_groups[name] = (
                _reconcile_group(name, merged_groups[name], group, strict=False) if name in merged_groups else group
            )

        merged_vouchers.extend(fragment.vouchers)

    if not merged_ledgers:
        raise TallyXmlNotATallyExportError(
            "No <LEDGER> master elements found in any of the uploaded files -- doesn't look like a Tally export."
        )

    known_ledger_names = set(merged_ledgers)
    for voucher in merged_vouchers:
        for leg in voucher.legs:
            if leg.ledger_name not in known_ledger_names:
                raise TallyXmlParseError(
                    f"Voucher '{voucher.voucher_number}' references ledger '{leg.ledger_name}', which has no "
                    f"matching <LEDGER> master in any of the uploaded files."
                )

    return TallyData(ledgers=merged_ledgers, vouchers=merged_vouchers, groups=merged_groups)


def parse_tally_xml_data_multi(files: List[bytes]) -> TallyData:
    """Entry point for a real-world Tally export split across multiple
    files (masters-only + transactions-only, in either order -- or any
    other split, or even just one combined file, since merging a single
    fragment is a no-op). Parses each of `files` as a fragment (see
    parse_tally_xml_fragment) and merges them (see
    merge_tally_xml_fragments).
    """
    if not files:
        raise TallyXmlParseError("No files supplied to parse.")
    fragments = [parse_tally_xml_fragment(f) for f in files]
    return merge_tally_xml_fragments(fragments)


def parse_tally_xml(xml_bytes: bytes) -> TrialBalance:
    """Parses a raw Tally XML export and collapses it into a TrialBalance of
    permanent (balance-sheet) ledgers' *closing* balances -- see module
    docstring "Balance-sheet vs P&L filtering" and "KNOWN LIMITATION" before
    using this against opening_balance_vs_prior_year_closing.py.
    """
    data = parse_tally_xml_data(xml_bytes)

    ledgers: List[LedgerBalance] = []
    for name, master in data.ledgers.items():
        if data.resolve_top_level_group(name) in PROFIT_AND_LOSS_PARENT_GROUPS:
            continue
        closing = data.closing_balance(name)
        debit = closing if closing >= 0 else Decimal("0.00")
        credit = -closing if closing < 0 else Decimal("0.00")
        ledgers.append(LedgerBalance(name=name, group=master.parent, debit=debit, credit=credit))

    if not ledgers:
        raise TallyXmlParseError("No permanent (balance-sheet) ledgers found after excluding Profit & Loss groups -- nothing to check.")

    ledgers.sort(key=lambda l: l.name)
    return TrialBalance(ledgers=ledgers)


def parse_tally_xml_file(path: str) -> TrialBalance:
    """Convenience wrapper: reads `path` as raw bytes and parses it. Raises
    OSError (e.g. FileNotFoundError) if the file can't be read, same as
    TrialBalance.from_csv."""
    return parse_tally_xml(Path(path).read_bytes())
