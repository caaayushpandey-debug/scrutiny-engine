#!/usr/bin/env python3
"""Anonymize real Tally XML exports -- swap identifying real-world values for
realistic fakes while leaving the file's STRUCTURE byte-for-byte intact.

Why this exists
---------------
We want to harden tally_xml_parser.py against the messy shapes real Tally
exports actually take (odd nesting, split masters/transactions files, UTF-16
BOMs, illegal control characters, unexpected voucher-type/entry-mode combos).
To study those safely we need real files with the real STRUCTURE but none of
the real IDENTITY. This script produces exactly that.

What counts as "structure" (NEVER touched)
------------------------------------------
Every tag, every attribute name, every level of nesting, whitespace, the XML
declaration, the byte-order mark and encoding, self-closing vs empty forms,
comments, and -- deliberately -- even the illegal control characters / bad
numeric entities real files contain (e.g. `&#4; Not Applicable`, a raw 0x05
in STATKEY -- see tally_xml_parser.py's _normalize_xml_encoding). Also
preserved as-is because the parser's analysis depends on them: VCHTYPE values,
ISDEEMEDPOSITIVE, every AMOUNT, every DATE, voucher numbers, and Tally's
group NAMES (Sundry Debtors, Sales Accounts, Bank Accounts, ...) -- the
balance-sheet-vs-P&L classification matches ledgers against these exact names,
so renaming them would corrupt the very behaviour we're trying to analyse.

What counts as "identity" (replaced, consistently)
--------------------------------------------------
Company name(s), GSTIN, PAN, bank account numbers (incl. <BANKDETAILS>), IFSC,
account-holder names, party names (Sundry Debtors/Creditors and any custom
vendor/employee/supplier sub-group beneath them, banks, loans, capital-account
partners -- detected by walking the FULL group-parent chain plus party
keywords, not just the resolved top-level group), postal addresses, e-mails,
phone numbers, and pincodes.

Split masters/transactions files -> ONE shared mapping
------------------------------------------------------
Real exports split a company across a masters file (<GROUP>/<LEDGER>) and a
transactions file (<VOUCHER>). A party ledger is DEFINED in the masters file
but only REFERENCED by name in the transactions file (<LEDGERNAME>). Anonymis-
ing each file independently would leak / desync those references. So --in-dir
mode builds ONE mapping by discovering across EVERY file in the set, then
applies that single mapping to each file. The company's own GSTIN, for
instance, appears only in the transactions files here -- shared discovery is
what makes it get replaced in both.

How structure survives while identity changes
---------------------------------------------
We never round-trip through an XML tree (which would reorder attributes,
normalise whitespace, drop the declaration, and choke on the illegal chars).
We decode the raw bytes (sniffing the BOM), discover the identifying strings,
build a deterministic real->fake mapping, and apply the WHOLE mapping in ONE
longest-match-first regex pass over the raw text. That single pass guarantees
referential integrity for free: a party renamed in its <LEDGER NAME="..">
master is renamed identically in every <LEDGERNAME> leg, <MAILINGNAME>,
<PARTYLEDGERNAME>, and any <NARRATION> mention -- because we replace the
literal string everywhere, and we only ever put identity strings (never
structural tokens) into the map.

Determinism
-----------
Each fake is derived from sha256(real_value), so re-running is identical and a
value shared across two companies' files anonymises the same way in both.

Usage
-----
    python3 anonymize_tally_xml.py INPUT.xml [OUTPUT.xml] [--report]
    python3 anonymize_tally_xml.py --in-dir SRC --out-dir DEST [--report]

--in-dir treats every *.xml directly in SRC as ONE related company set (shared
mapping) and writes anonymised copies into DEST with the same filenames.
--report prints the real->fake mapping (to stderr) so a human can eyeball what
changed -- USE IT to confirm no structural ledger (e.g. "Cash") was mapped.

KNOWN RESIDUAL RISKS (surfaced deliberately, not hidden)
--------------------------------------------------------
- A person named ONLY inside a <NARRATION> (never as a ledger/party) can't be
  discovered structurally. Narrations are scrubbed of every discovered value
  plus e-mail/phone/GSTIN/PAN patterns and 6+ digit runs; use
  --blank-narrations to replace narration text with a placeholder instead.
- GUID / REMOTEID / VCHKEY / ALTERID carry a per-company UUID that recurs
  across the files (a correlator, though not human-readable identity). Left
  intact by default; pass --scrub-guids to randomise them consistently.
Nothing here imports anything outside the Python standard library.
"""
import argparse
import codecs
import hashlib
import html
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# --------------------------------------------------------------------------
# Encoding: sniff and preserve exactly (the parser throws the BOM away; we
# must not -- we write it back so the file stays byte-shaped like the real one).
# --------------------------------------------------------------------------
def decode_preserving_encoding(raw: bytes) -> Tuple[str, str, bytes]:
    if raw.startswith(codecs.BOM_UTF16_LE):
        return raw[len(codecs.BOM_UTF16_LE):].decode("utf-16-le"), "utf-16-le", codecs.BOM_UTF16_LE
    if raw.startswith(codecs.BOM_UTF16_BE):
        return raw[len(codecs.BOM_UTF16_BE):].decode("utf-16-be"), "utf-16-be", codecs.BOM_UTF16_BE
    if raw.startswith(codecs.BOM_UTF8):
        return raw[len(codecs.BOM_UTF8):].decode("utf-8"), "utf-8", codecs.BOM_UTF8
    return raw.decode("utf-8"), "utf-8", b""


def encode_preserving_encoding(text: str, codec_name: str, bom: bytes) -> bytes:
    return bom + text.encode(codec_name)


# --------------------------------------------------------------------------
# Identity-bearing tags. Content between <TAG>...</TAG> is treated as an
# identifying value of the given CATEGORY. NO structural tags appear here.
# Deliberately EXCLUDED: PARTYLEDGERNAME / PARTYNAME -- in transactions those
# can point at a STRUCTURAL ledger ("Cash", the company itself), so renaming
# them blindly would rename structural ledgers. Real parties are caught
# authoritatively from the masters' group structure (see party detection).
# --------------------------------------------------------------------------
TAG_CATEGORIES: Dict[str, str] = {
    # Company (the entity whose books these are)
    "SVCURRENTCOMPANY": "company", "CURRENTCOMPANY": "company", "COMPANYNAME": "company",
    "CMPNAME": "company", "REMOTECMPNAME": "company", "SVFROMCOMPANY": "company",
    "BASICCOMPANYFORMALNAME": "company",
    # Genuine external-party names typed onto a voucher/ledger (not ledger refs)
    "BASICBUYERNAME": "person", "CONSIGNEE": "person", "BANKACCHOLDERNAME": "person",
    # Contact PII
    "EMAIL": "email", "EMAILCC": "email",
    "PHONENUMBER": "phone", "LEDGERPHONE": "phone", "LEDGERMOBILE": "phone",
    "MOBILENO": "phone", "LEDGERCONTACT": "phone", "FAXNUMBER": "phone",
    "ADDRESS": "address", "PINCODE": "pincode", "PARTYPINCODE": "pincode",
    # Bank / tax identifiers
    "IFSCODE": "ifsc", "IFSC": "ifsc",
    "BANKDETAILS": "account", "ACCOUNTNUMBER": "account", "BANKACCOUNTNUMBER": "account",
    "ACCNO": "account", "BANKACCNO": "account",
    "GSTIN": "gstin", "PARTYGSTIN": "gstin", "CMPGSTIN": "gstin",
    "GSTREGISTRATIONNUMBER": "gstin", "GSTINNO": "gstin",
    "INCOMETAXNUMBER": "pan", "PANNUMBER": "pan", "LEDGERPAN": "pan",
}

# Names inside a party ledger master that also carry the party's identity.
PARTY_NAME_SUBTAGS = ("MAILINGNAME", "OLDMAILINGNAME", "OLDLEDGERNAME", "ALIAS")

# Structural / account-type words that are NOT part of a person's name -- used
# to filter tokens when harvesting person names for token propagation, so
# "Capital"/"Loan"/"Remuneration" never become a person token that would drag
# in unrelated structural ledgers.
NAME_STOPWORDS = {
    "capital", "account", "accounts", "loan", "loans", "advance", "advances",
    "remuneration", "salary", "salaries", "wages", "commission", "bonus",
    "incentive", "incentives", "reimbursement", "drawings", "current", "liability",
    "liabilities", "asset", "assets", "payable", "receivable", "unsecured", "secured",
    "deposit", "deposits", "opening", "closing", "sundry", "creditor", "creditors",
    "debtor", "debtors", "bank", "cash", "provision", "provisions", "expense",
    "expenses", "income", "charges", "fees", "duties", "purchase", "purchases",
    "sales", "general", "reserve", "party", "limited", "private", "enterprises",
    "traders", "vendor", "vendors", "supplier", "suppliers", "services", "service",
    "company", "corporation", "industries", "chef", "staff", "employee", "employees",
    "shri", "smt", "mrs", "from",
}
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z']+")


def _name_tokens(s: str) -> List[str]:
    """Candidate person-name tokens: alphabetic, >=4 chars, not a structural word."""
    return [w for w in _TOKEN_RE.findall(html.unescape(s))
            if len(w) >= 4 and w.lower() not in NAME_STOPWORDS]

# Built-in Tally groups (or any ancestor thereof) whose ledgers name external
# parties/persons. Compared against UNESCAPED group names.
PARTY_GROUP_NAMES = {
    "Sundry Debtors", "Sundry Creditors", "Bank Accounts", "Bank OD A/c",
    "Bank OCC A/c", "Secured Loans", "Unsecured Loans", "Loans (Liability)",
    "Loans & Advances (Asset)", "Deposits (Asset)", "Capital Account",
}
BANK_GROUP_NAMES = {"Bank Accounts", "Bank OD A/c", "Bank OCC A/c"}
# Custom sub-group names that mark parties even when they resolve up into a
# broad group like "Current Liabilities" (real: "Payable to Employees" ->
# Current Liabilities; "Food & Grocery Vendors" -> Sundry Creditors -> Current
# Liabilities). Matched case-insensitively against UNESCAPED group names.
PARTY_KEYWORDS = re.compile(
    r"vendor|supplier|debtor|creditor|customer|employee|payable to|receivable|"
    r"\bstaff\b|partner|proprietor|director|payable from",
    re.IGNORECASE,
)
BANK_KEYWORD = re.compile(r"\bbank\b", re.IGNORECASE)

FREE_TEXT_TAGS = ("NARRATION",)

# Value patterns (used both to discover and to scrub free text)
GSTIN_RE = re.compile(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]\b")
PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
IFSC_RE = re.compile(r"\b[A-Z]{4}0[0-9A-Z]{6}\b")
LONG_DIGITS_RE = re.compile(r"\d{6,}")
GUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")

MIN_NAME_LEN = 4


def _extract_tag_content(text: str, tag: str) -> List[str]:
    """LEAF text of <tag>...</tag> only. The capture is [^<]* (stops at any
    '<'), so a same-named CONTAINER element -- e.g. real Tally's
    `<GSTIN NAME="27..">...<LANGUAGENAME.LIST>..</GSTIN>` GST-registration
    master, where the identifier is in the attribute and the body holds child
    elements -- is deliberately NOT matched. Capturing across children and
    replacing it as one "value" would delete real markup and corrupt
    structure. Identifiers that live in an attribute (like that GSTIN) are
    still caught by the pattern matchers in discover_into, which scan the raw
    text regardless of where the value sits."""
    pat = re.compile(r"<" + re.escape(tag) + r"(?:\s[^>]*)?>([^<]*)</" + re.escape(tag) + r">",
                     re.IGNORECASE)
    return [m.strip() for m in pat.findall(text) if m.strip()]


# --------------------------------------------------------------------------
# Party-ledger discovery: walk each ledger's FULL PARENT chain through the
# GROUP masters, classifying it party/bank if any ancestor is a party group or
# matches a party keyword. Returns {escaped_name: category} for party ledgers,
# plus their in-master name sub-tags (MAILINGNAME etc.).
# --------------------------------------------------------------------------
def _block_name_parent_map(text: str, element: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    block_re = re.compile(
        r"<" + element + r"\b[^>]*\bNAME\s*=\s*\"(.*?)\"[^>]*>(.*?)</" + element + r">",
        re.IGNORECASE | re.DOTALL,
    )
    parent_re = re.compile(r"<PARENT(?:\s[^>]*)?>(.*?)</PARENT>", re.IGNORECASE | re.DOTALL)
    for name, body in block_re.findall(text):
        m = parent_re.search(body)
        result[name.strip()] = (m.group(1).strip() if m else "")
    return result


def _ancestor_chain(name: str, group_parents: Dict[str, str]) -> List[str]:
    """Every group name from `name` up to the top (inclusive), escaped forms."""
    chain: List[str] = []
    seen = set()
    current = name
    while current and current not in seen:
        chain.append(current)
        seen.add(current)
        current = group_parents.get(current, "")
    return chain


def _ledger_subtag_names(body: str) -> List[str]:
    out = []
    for sub in PARTY_NAME_SUBTAGS:
        for vm in re.finditer(r"<" + sub + r"(?:\s[^>]*)?>([^<]*)</" + sub + r">", body, re.IGNORECASE):
            v = vm.group(1).strip()
            if v:
                out.append(v)
    return out


def _classify_party_ledgers(text: str) -> Dict[str, str]:
    """{escaped ledger/name value -> 'person'|'bank'} for every party ledger
    and its identity sub-tags. Empty if this file has no <LEDGER> masters
    (a transactions-only fragment).

    Detection is in three passes: (1) group chain + keyword; (2) harvest
    person-name tokens from the confirmed PERSON ledgers; (3) token
    propagation -- any OTHER ledger whose name contains a harvested token is a
    person too (catches e.g. 'Sunita Agarwal - Remuneration' filed directly
    under Current Liabilities, once 'Sunita' is known from her Capital A/c
    ledger -- even across a surname spelling change)."""
    ledger_parents = _block_name_parent_map(text, "LEDGER")
    group_parents = _block_name_parent_map(text, "GROUP")
    out: Dict[str, str] = {}
    if not ledger_parents:
        return out

    ledger_bodies: Dict[str, str] = {}
    for m in re.finditer(r"<LEDGER\b[^>]*\bNAME\s*=\s*\"(.*?)\"[^>]*>(.*?)</LEDGER>", text, re.DOTALL):
        ledger_bodies[m.group(1).strip()] = m.group(2)

    # Pass 1: group-chain + keyword classification
    party: Dict[str, str] = {}
    for ledger_name, parent in ledger_parents.items():
        chain = [html.unescape(c) for c in (_ancestor_chain(parent, group_parents) if parent else [])]
        if not (any(c in PARTY_GROUP_NAMES for c in chain) or any(PARTY_KEYWORDS.search(c) for c in chain)):
            continue
        is_bank = any(c in BANK_GROUP_NAMES for c in chain) or any(BANK_KEYWORD.search(c) for c in chain)
        party[ledger_name] = "bank" if is_bank else "person"

    # Pass 2: harvest person-name tokens from confirmed PERSON ledgers (+ their names)
    person_tokens: set = set()
    for name, cat in party.items():
        if cat != "person":
            continue
        for tok in _name_tokens(name):
            person_tokens.add(tok.lower())
        for v in _ledger_subtag_names(ledger_bodies.get(name, "")):
            for tok in _name_tokens(v):
                person_tokens.add(tok.lower())

    # Pass 3: token propagation onto not-yet-classified ledgers
    if person_tokens:
        for ledger_name in ledger_parents:
            if ledger_name in party:
                continue
            if any(t.lower() in person_tokens for t in _name_tokens(ledger_name)):
                party[ledger_name] = "person"

    # Pass 4: emit every party ledger name + its identity sub-tags
    for name, cat in party.items():
        if len(name) >= MIN_NAME_LEN:
            out[name] = cat
        for v in _ledger_subtag_names(ledger_bodies.get(name, "")):
            if len(v) >= MIN_NAME_LEN:
                out.setdefault(v, cat)
    return out


# --------------------------------------------------------------------------
# Deterministic, format-preserving fake generators.
# --------------------------------------------------------------------------
ADJECTIVES = ["Ironwood", "Meridian", "Silverline", "Bridgeway", "Zenora", "Vantage",
              "Kinetic", "Northgate", "Everest", "Cobalt", "Harbour", "Solstice",
              "Emberly", "Larkfield", "Novacrest", "Pinnacle", "Aster", "Copperfield"]
NOUNS = ["Solutions", "Industries", "Traders", "Enterprises", "Distributors", "Consultants",
         "Hospitality", "Foods", "Ventures", "Provisions", "Overseas", "Associates",
         "Systems", "Holdings", "Retail", "Exports", "Agro", "Commercial"]
# Large enough pools that the injective-mapping loop in build_mapping (which
# guarantees two distinct real names never collapse to one fake -- that would
# create a duplicate ledger master and corrupt the file) has ample headroom
# for the few hundred parties a real company set contains.
FIRST_NAMES = ["Arjun", "Priya", "Rohan", "Neha", "Vikram", "Anita", "Karan", "Deepa",
               "Sanjay", "Meera", "Rahul", "Kavya", "Amit", "Pooja", "Nikhil", "Sneha",
               "Aditya", "Riya", "Varun", "Isha", "Manish", "Tara", "Gaurav", "Divya",
               "Harsh", "Nisha", "Kunal", "Ritu", "Siddharth", "Payal", "Ashok", "Rekha",
               "Vivek", "Shreya", "Naveen", "Anjali", "Rakesh", "Preeti", "Sameer", "Jyoti",
               "Yash", "Aarti", "Dev", "Lata", "Mohit", "Swati"]
LAST_NAMES = ["Sharma", "Iyer", "Nair", "Reddy", "Kapoor", "Menon", "Bose", "Gupta",
              "Malhotra", "Rao", "Verma", "Joshi", "Pillai", "Chopra", "Desai", "Shah",
              "Agarwal", "Bhat", "Chauhan", "Dubey", "Ghosh", "Hegde", "Jain", "Khanna",
              "Lal", "Mehta", "Naidu", "Oberoi", "Patel", "Qureshi", "Rana", "Saxena",
              "Trivedi", "Uppal", "Varma", "Wagh", "Yadav", "Zaveri", "Kulkarni", "Sinha",
              "Bajaj", "Chawla", "Dutta", "Grover", "Kamath", "Mistry"]
CITIES = ["Pune", "Nagpur", "Indore", "Surat", "Vadodara", "Nashik", "Rajkot", "Coimbatore"]
STREETS = ["Industrial Estate", "Business Park", "Commercial Complex", "Trade Centre",
           "Corporate Avenue", "MIDC Area", "Market Road", "Tech Hub"]
LEGAL_SUFFIXES = [
    ("private limited", "Private Limited"), ("pvt. ltd.", "Pvt. Ltd."), ("pvt ltd", "Pvt Ltd"),
    ("limited", "Limited"), (" ltd.", " Ltd."), (" ltd", " Ltd"), (" llp", " LLP"),
    ("& co.", "& Co."), ("& co", "& Co"), ("enterprises", "Enterprises"),
    ("industries", "Industries"), ("associates", "Associates"),
]


def _rng(value: str):
    import random
    return random.Random(int(hashlib.sha256(value.encode("utf-8")).hexdigest(), 16))


# Every faker takes (real, seed=None): `real` is the TEMPLATE (its length /
# digit positions / state code are mirrored), `seed` drives the RNG. The
# injective loop in build_mapping re-rolls a collision by passing a salted
# seed while keeping `real` as the template, so the retry stays format-correct.
def _scramble_digits(real: str, seed: Optional[str] = None) -> str:
    r = _rng(seed if seed is not None else real)
    return re.sub(r"\d", lambda _m: str(r.randint(0, 9)), real)


def _fake_company(real: str, seed: Optional[str] = None) -> str:
    r = _rng(seed if seed is not None else real)
    lower = html.unescape(real).lower()
    suffix = ""
    for needle, pretty in LEGAL_SUFFIXES:
        if needle in lower:
            suffix = " " + pretty.strip()
            break
    return f"{r.choice(ADJECTIVES)} {r.choice(NOUNS)}{suffix}"


def _fake_person(real: str, seed: Optional[str] = None) -> str:
    # Deliberately preserves NO substring of the real value -- an earlier
    # version kept the text before a "-" as a "structural label", but real
    # ledgers like "Sunita Agarwal - Remuneration" put the NAME before the
    # dash and a structural word after it, so that leaked the real name. A
    # clean full fake is the only leak-proof choice; a lost "Loan - " / "Sundry
    # Creditor - " prefix is an acceptable cosmetic cost.
    r = _rng(seed if seed is not None else real)
    return f"{r.choice(FIRST_NAMES)} {r.choice(LAST_NAMES)}"


def _fake_gstin(real: str, seed: Optional[str] = None) -> str:
    r = _rng(seed if seed is not None else real)
    state = real[:2] if len(real) >= 2 and real[:2].isdigit() else "27"  # keep state code
    letters = "".join(r.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(5))
    digits = "".join(r.choice("0123456789") for _ in range(4))
    entity = r.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    check = r.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    last = r.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    return f"{state}{letters}{digits}{entity}{check}Z{last}"


def _fake_pan(real: str, seed: Optional[str] = None) -> str:
    r = _rng(seed if seed is not None else real)
    return ("".join(r.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(5))
            + "".join(r.choice("0123456789") for _ in range(4))
            + r.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))


def _fake_ifsc(real: str, seed: Optional[str] = None) -> str:
    r = _rng(seed if seed is not None else real)
    return ("".join(r.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(4)) + "0"
            + "".join(r.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(6)))


def _fake_email(real: str, seed: Optional[str] = None) -> str:
    r = _rng(seed if seed is not None else real)
    return "".join(r.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(r.randint(5, 9))) + "@example.com"


def _fake_address(real: str, seed: Optional[str] = None) -> str:
    r = _rng(seed if seed is not None else real)
    return f"{r.randint(1, 999)}, {r.choice(STREETS)}, {r.choice(CITIES)}"


# 'bank' keeps the bank brand and structure, only digits change (so an account
# tail in a ledger name is scrambled but "HDFC Bank ... (Andheri)" stays a bank).
FAKERS = {
    "company": _fake_company, "person": _fake_person, "bank": _scramble_digits,
    "gstin": _fake_gstin, "pan": _fake_pan, "ifsc": _fake_ifsc,
    "account": _scramble_digits, "phone": _scramble_digits, "pincode": _scramble_digits,
    "email": _fake_email, "address": _fake_address,
}


def _company_core(values: List[str]) -> Optional[str]:
    """The shared core company name across its branch variants, e.g.
    'T-Leaf Service Private Limited' from '... - Andheri' / '...-Bayleaf Cafe'."""
    vals = sorted(set(values))
    if not vals:
        return None
    core = os.path.commonprefix(vals) if len(vals) >= 2 else re.split(r"\s[-,–]\s", vals[0])[0]
    core = core.rstrip(" -,–:|•").strip()
    return core if len(core) >= MIN_NAME_LEN else None


# --------------------------------------------------------------------------
# Discovery + application
# --------------------------------------------------------------------------
def discover_into(text: str, value_category: Dict[str, str]) -> None:
    """Accumulate identifying value -> category from one file's text. Called
    once per file so a whole split set shares one mapping."""
    def note(value: str, category: str):
        value = value.strip()
        if not value:
            return
        if category in ("company", "person", "bank") and len(value) < MIN_NAME_LEN:
            return
        value_category.setdefault(value, category)  # first-seen category wins

    for v in GSTIN_RE.findall(text):
        note(v, "gstin")
    for v in EMAIL_RE.findall(text):
        note(v, "email")
    for v in IFSC_RE.findall(text):
        note(v, "ifsc")
    for v in PAN_RE.findall(text):
        note(v, "pan")
    for tag, category in TAG_CATEGORIES.items():
        for v in _extract_tag_content(text, tag):
            note(v, category)
    for value, category in _classify_party_ledgers(text).items():
        note(value, category)


def build_mapping(value_category: Dict[str, str], scrub_guids: bool, all_text: str = "") -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    used: set = set()  # every fake already assigned -> keeps the mapping INJECTIVE

    def assign(value: str, category: str) -> None:
        """Map `value` to a format-correct fake that no OTHER value already
        uses. Two distinct real names collapsing to one fake would create a
        duplicate ledger master and corrupt the file, so we re-roll (salted
        seed, same template) until the fake is unique."""
        gen = FAKERS[category]
        salt = 0
        while True:
            cand = gen(value, None if salt == 0 else f"{value}#\x00{salt}")
            if cand not in used:
                break
            salt += 1
        used.add(cand)
        mapping[value] = cand

    company_values = [v for v, c in value_category.items() if c == "company"]
    core = _company_core(company_values)
    fake_core = _fake_company(core) if core else None
    if fake_core:
        used.add(fake_core)

    # Deterministic order (sorted) so the run is reproducible regardless of
    # dict insertion order.
    for value, category in sorted(value_category.items()):
        if category == "company" and fake_core:
            mapping[value] = fake_core           # every branch variant -> ONE fake core (intentional)
        else:
            assign(value, category)
    if core and fake_core:
        mapping[core] = fake_core                # bare core occurrences too

    if scrub_guids and all_text:
        for g in sorted(set(GUID_RE.findall(all_text))):
            if g not in mapping:
                assign(g, "account")
    return mapping


def apply_mapping(text: str, big: "re.Pattern[str]", mapping: Dict[str, str]) -> str:
    if not mapping:
        return text
    return big.sub(lambda m: mapping[m.group(0)], text)


def scrub_free_text(text: str, blank: bool, name_ci: Optional["re.Pattern[str]"] = None,
                    name_ci_map: Optional[Dict[str, str]] = None) -> str:
    """Extra safety pass over <NARRATION>: blank it (--blank-narrations), or
    scrub residual PII discovery can't catch structurally -- discovered names
    that appear here in a DIFFERENT CASE (e.g. 'Bayleaf cafe' vs the ledger's
    'Bayleaf Cafe'), plus pattern-shaped PII (e-mail/GSTIN/PAN) and 6+ digit
    runs (UPI refs, card/account numbers). apply_mapping already replaced
    exact-case occurrences; this mops up the rest.

    NOTE even with scrubbing, a person named ONLY in a narration and never as
    a ledger (e.g. 'Sunita' in 'Prov of PTEC of Sunita') cannot be discovered
    structurally and would survive -- --blank-narrations is the only way to
    guarantee no narration leak. For real client data, prefer blanking."""
    for tag in FREE_TEXT_TAGS:
        pat = re.compile(r"(<" + tag + r"(?:\s[^>]*)?>)(.*?)(</" + tag + r">)",
                         re.IGNORECASE | re.DOTALL)

        def _repl(m: "re.Match[str]") -> str:
            if blank:
                return m.group(1) + "[redacted]" + m.group(3)
            inner = m.group(2)
            if name_ci is not None and name_ci_map:
                inner = name_ci.sub(lambda x: name_ci_map.get(x.group(0).lower(), x.group(0)), inner)
            inner = GSTIN_RE.sub(lambda x: _fake_gstin(x.group(0)), inner)
            inner = PAN_RE.sub(lambda x: _fake_pan(x.group(0)), inner)
            inner = EMAIL_RE.sub(lambda x: _fake_email(x.group(0)), inner)
            inner = LONG_DIGITS_RE.sub(lambda x: _scramble_digits(x.group(0)), inner)
            return m.group(1) + inner + m.group(3)

        text = pat.sub(_repl, text)
    return text


def _compile_big(mapping: Dict[str, str]) -> "re.Pattern[str]":
    keys = sorted(mapping.keys(), key=len, reverse=True)  # longest-match-first
    return re.compile("|".join(re.escape(k) for k in keys)) if keys else re.compile(r"(?!x)x")


def _print_report(mapping: Dict[str, str]) -> None:
    print(f"\n=== {len(mapping)} value(s) in shared mapping ===", file=sys.stderr)
    for real, fake in sorted(mapping.items()):
        shown = real if len(real) <= 70 else real[:67] + "..."
        print(f"  {shown!r}  ->  {fake!r}", file=sys.stderr)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _anonymize_set(files: List[Path], out_paths: List[Path], report: bool,
                   blank: bool, scrub_guids: bool) -> None:
    """Shared-mapping anonymization across a related set of files."""
    # 1) discovery over every file (release each file's text after)
    value_category: Dict[str, str] = {}
    decoded: List[Tuple[str, str, bytes]] = []  # keep for guid-scan/apply reuse of masters only
    guid_text_parts: List[str] = []
    for f in files:
        t0 = time.time()
        raw = f.read_bytes()
        text, codec_name, bom = decode_preserving_encoding(raw)
        discover_into(text, value_category)
        if scrub_guids:
            guid_text_parts.append("\n".join(sorted(set(GUID_RE.findall(text)))))
        print(f"  discovered {f.name}  ({len(raw):,} bytes, {time.time()-t0:.1f}s)", file=sys.stderr)
        del text  # free big transaction-file text before the next one

    mapping = build_mapping(value_category, scrub_guids, "\n".join(guid_text_parts))
    big = _compile_big(mapping)

    # Case-insensitive name pattern for the narration scrub (non-blank mode):
    # catches a discovered name that recurs in a narration in a different case.
    name_keys = [v for v, c in value_category.items() if c in ("company", "person", "bank")]
    core = _company_core([v for v, c in value_category.items() if c == "company"])
    if core:
        name_keys.append(core)
    name_ci_map = {k.lower(): mapping[k] for k in name_keys if k in mapping}
    name_ci = (re.compile("|".join(re.escape(k) for k in sorted(name_keys, key=len, reverse=True)),
                          re.IGNORECASE) if name_keys else None)
    if report:
        _print_report(mapping)

    # 2) apply to each file
    for f, out in zip(files, out_paths):
        t0 = time.time()
        text, codec_name, bom = decode_preserving_encoding(f.read_bytes())
        text = apply_mapping(text, big, mapping)
        text = scrub_free_text(text, blank, name_ci, name_ci_map)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(encode_preserving_encoding(text, codec_name, bom))
        print(f"Wrote {out}  ({time.time()-t0:.1f}s)")
        del text


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Anonymize real Tally XML exports, structure-preserving.")
    ap.add_argument("input", nargs="?", help="Input .xml file (single-file mode)")
    ap.add_argument("output", nargs="?", help="Output .xml (default: alongside input, .anon.xml)")
    ap.add_argument("--in-dir", help="Treat every *.xml in this dir as ONE company set (shared mapping)")
    ap.add_argument("--out-dir", help="Write anonymized copies here (required with --in-dir)")
    ap.add_argument("--report", action="store_true", help="Print the real->fake mapping to stderr")
    ap.add_argument("--blank-narrations", action="store_true", help="Replace all <NARRATION> text with [redacted]")
    ap.add_argument("--scrub-guids", action="store_true", help="Also randomise GUID/REMOTEID/VCHKEY UUIDs")
    args = ap.parse_args(argv)

    if args.in_dir:
        if not args.out_dir:
            ap.error("--in-dir requires --out-dir")
        src = Path(args.in_dir)
        dest = Path(args.out_dir)
        if not src.is_dir():
            ap.error(f"--in-dir: {src} is not a directory")
        xmls = sorted(p for p in src.glob("*.xml"))
        if not xmls:
            print(f"No *.xml files directly in {src}", file=sys.stderr)
            return 1
        _anonymize_set(xmls, [dest / p.name for p in xmls], args.report,
                       args.blank_narrations, args.scrub_guids)
        return 0

    if not args.input:
        ap.error("provide an input file, or --in-dir with --out-dir")
    inp = Path(args.input)
    out = Path(args.output) if args.output else inp.with_suffix(".anon.xml")
    _anonymize_set([inp], [out], args.report, args.blank_narrations, args.scrub_guids)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
