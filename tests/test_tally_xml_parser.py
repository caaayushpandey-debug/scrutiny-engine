import unittest
from decimal import Decimal

from tally_xml_parser import (
    TallyXmlEncodingError,
    TallyXmlMalformedError,
    TallyXmlNotATallyExportError,
    TallyXmlParseError,
    TallyXmlTruncatedError,
    _normalize_xml_encoding,
    _strip_invalid_numeric_entities,
    _strip_raw_illegal_control_chars,
    merge_tally_xml_fragments,
    parse_tally_xml,
    parse_tally_xml_data,
    parse_tally_xml_data_multi,
    parse_tally_xml_fragment,
)

ENVELOPE_OPEN = b"""<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
  <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
  <BODY><IMPORTDATA><REQUESTDATA>
"""
ENVELOPE_CLOSE = b"""
  </REQUESTDATA></IMPORTDATA></BODY>
</ENVELOPE>
"""


def ledger_xml(name, parent, opening):
    return f"""
    <TALLYMESSAGE xmlns:UDF="TallyUDF">
      <LEDGER NAME="{name}" ACTION="Create">
        <PARENT>{parent}</PARENT>
        <OPENINGBALANCE>{opening}</OPENINGBALANCE>
        <ISBILLWISEON>No</ISBILLWISEON>
      </LEDGER>
    </TALLYMESSAGE>
    """.encode()


def group_xml(name, parent):
    return f"""
    <TALLYMESSAGE xmlns:UDF="TallyUDF">
      <GROUP NAME="{name}" ACTION="Create">
        <PARENT>{parent}</PARENT>
      </GROUP>
    </TALLYMESSAGE>
    """.encode()


def voucher_xml(vch_type, vn, legs):
    """legs: list of (ledger_name, is_debit, amount_str)"""
    entries = "".join(f"""
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{name}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>{"Yes" if is_debit else "No"}</ISDEEMEDPOSITIVE>
          <AMOUNT>{amount}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>
    """ for name, is_debit, amount in legs)
    return f"""
    <TALLYMESSAGE xmlns:UDF="TallyUDF">
      <VOUCHER VCHTYPE="{vch_type}" ACTION="Create">
        <DATE>20250401</DATE>
        <VOUCHERTYPENAME>{vch_type}</VOUCHERTYPENAME>
        <VOUCHERNUMBER>{vn}</VOUCHERNUMBER>
        <NARRATION>Test voucher.</NARRATION>
        {entries}
      </VOUCHER>
    </TALLYMESSAGE>
    """.encode()


def to_utf16(xml_bytes: bytes, byte_order: str = "le") -> bytes:
    """Re-encodes a UTF-8 test fixture (as produced by build_xml) as UTF-16
    with a byte-order mark -- confirmed against real user files (2026-08-06)
    to be how real Tally exports are actually encoded, not UTF-8."""
    text = xml_bytes.decode("utf-8").replace('encoding="UTF-8"', 'encoding="UTF-16"')
    if byte_order == "le":
        return b"\xff\xfe" + text.encode("utf-16-le")
    return b"\xfe\xff" + text.encode("utf-16-be")


def build_xml(*parts) -> bytes:
    return ENVELOPE_OPEN + b"".join(parts) + ENVELOPE_CLOSE


class ClosingBalanceComputationTests(unittest.TestCase):
    def test_opening_balance_with_no_vouchers_passes_through(self):
        xml = build_xml(ledger_xml("HDFC Bank", "Bank Accounts", "100000.00"))
        tb = parse_tally_xml(xml)
        self.assertEqual(len(tb.ledgers), 1)
        self.assertEqual(tb.ledgers[0].net_balance, Decimal("100000.00"))

    def test_debit_leg_increases_debit_positive_balance(self):
        xml = build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "100000.00"),
            ledger_xml("Sales Account", "Sales Accounts", "0.00"),
            voucher_xml("Receipt", "RCT-0001", [("HDFC Bank", True, "-5000.00"), ("Sales Account", False, "5000.00")]),
        )
        tb = parse_tally_xml(xml)
        bank = next(l for l in tb.ledgers if l.name == "HDFC Bank")
        self.assertEqual(bank.net_balance, Decimal("105000.00"))

    def test_credit_leg_decreases_debit_positive_balance(self):
        xml = build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "100000.00"),
            ledger_xml("Rent Expense", "Indirect Expenses", "0.00"),
            voucher_xml("Payment", "PMT-0001", [("Rent Expense", True, "-8000.00"), ("HDFC Bank", False, "8000.00")]),
        )
        tb = parse_tally_xml(xml)
        bank = next(l for l in tb.ledgers if l.name == "HDFC Bank")
        self.assertEqual(bank.net_balance, Decimal("92000.00"))

    def test_multiple_vouchers_accumulate(self):
        xml = build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "0.00"),
            ledger_xml("Sales Account", "Sales Accounts", "0.00"),
            voucher_xml("Receipt", "RCT-0001", [("HDFC Bank", True, "-1000.00"), ("Sales Account", False, "1000.00")]),
            voucher_xml("Receipt", "RCT-0002", [("HDFC Bank", True, "-2500.50"), ("Sales Account", False, "2500.50")]),
        )
        tb = parse_tally_xml(xml)
        bank = next(l for l in tb.ledgers if l.name == "HDFC Bank")
        self.assertEqual(bank.net_balance, Decimal("3500.50"))

    def test_credit_natured_ledger_produces_credit_balance(self):
        xml = build_xml(
            ledger_xml("Sundry Creditors - ABC", "Sundry Creditors", "-50000.00"),
            ledger_xml("Purchase Account", "Purchase Accounts", "0.00"),
        )
        tb = parse_tally_xml(xml)
        row = tb.ledgers[0]
        self.assertEqual(row.debit, Decimal("0.00"))
        self.assertEqual(row.credit, Decimal("50000.00"))


class ProfitAndLossFilteringTests(unittest.TestCase):
    def test_pl_ledgers_excluded_from_output(self):
        xml = build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"),
            ledger_xml("Sales Account", "Sales Accounts", "0.00"),
            ledger_xml("Rent Expense", "Indirect Expenses", "0.00"),
            ledger_xml("Other Income", "Indirect Incomes", "0.00"),
            ledger_xml("Freight &amp; Forwarding", "Direct Expenses", "0.00"),
        )
        tb = parse_tally_xml(xml)
        self.assertEqual([l.name for l in tb.ledgers], ["HDFC Bank"])

    def test_only_pl_ledgers_raises(self):
        xml = build_xml(ledger_xml("Sales Account", "Sales Accounts", "0.00"))
        with self.assertRaises(TallyXmlParseError):
            parse_tally_xml(xml)


class EmptyParentLedgerTests(unittest.TestCase):
    """Covers a real client bug (2026-08-09): a ledger whose <PARENT> is
    present but empty/self-closing (<PARENT/>) was wrongly rejected the
    same way as the tag being entirely absent -- "missing required
    <PARENT> element". Confirmed real example: Tally's own reserved
    "Profit & Loss A/c" ledger genuinely has no parent group at all, and a
    real export represents that with an empty <PARENT/>, not by omitting
    the tag. See _required_element_optional_text in tally_xml_parser.py."""

    def test_ledger_with_self_closing_parent_tag_parses_successfully(self):
        # The exact real-world shape: <PARENT/>, self-closing.
        xml = ENVELOPE_OPEN + b"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="Profit &amp; Loss A/c" ACTION="Create">
            <PARENT/>
            <OPENINGBALANCE>-125000.00</OPENINGBALANCE>
          </LEDGER>
        </TALLYMESSAGE>
        """ + ENVELOPE_CLOSE
        data = parse_tally_xml_data(xml)
        self.assertIn("Profit & Loss A/c", data.ledgers)
        self.assertEqual(data.ledgers["Profit & Loss A/c"].parent, "")
        self.assertEqual(data.ledgers["Profit & Loss A/c"].opening_balance, Decimal("-125000.00"))

    def test_ledger_with_empty_open_close_parent_tag_also_parses(self):
        # <PARENT></PARENT> (not self-closing) -- ElementTree treats this
        # identically to <PARENT/>, but worth covering explicitly rather
        # than assuming that holds.
        xml = build_xml(ledger_xml("Profit &amp; Loss A/c", "", "-125000.00"))
        data = parse_tally_xml_data(xml)
        self.assertEqual(data.ledgers["Profit & Loss A/c"].parent, "")

    def test_ledger_with_parent_element_completely_absent_still_raises(self):
        # Distinct from the empty-tag case above: the ELEMENT itself
        # missing entirely is still a structural problem, not a legitimate
        # "no parent group" state -- see _required_element_optional_text's
        # docstring for why these are treated differently.
        xml = ENVELOPE_OPEN + b"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="Some Ledger" ACTION="Create">
            <OPENINGBALANCE>1000.00</OPENINGBALANCE>
          </LEDGER>
        </TALLYMESSAGE>
        """ + ENVELOPE_CLOSE
        with self.assertRaises(TallyXmlParseError) as ctx:
            parse_tally_xml_data(xml)
        self.assertIn("missing required <PARENT>", str(ctx.exception))

    def test_ledger_with_empty_parent_not_misclassified_as_profit_and_loss(self):
        # A ledger with no parent group at all must NOT be silently
        # excluded from parse_tally_xml's permanent (balance-sheet) trial
        # balance -- resolve_top_level_group("") must not accidentally
        # match anything in PROFIT_AND_LOSS_PARENT_GROUPS.
        xml = build_xml(ledger_xml("Profit &amp; Loss A/c", "", "-125000.00"))
        tb = parse_tally_xml(xml)
        self.assertEqual(len(tb.ledgers), 1)
        self.assertEqual(tb.ledgers[0].name, "Profit & Loss A/c")

    def test_voucher_can_reference_ledger_with_empty_parent(self):
        # The reserved ledger isn't just a static balance -- vouchers can
        # legitimately touch it too (e.g. year-end transfers), so closing
        # balance computation must work the same as for any other ledger.
        xml = build_xml(
            ledger_xml("Profit &amp; Loss A/c", "", "-125000.00"),
            ledger_xml("Reserves and Surplus", "Reserves and Surplus", "0.00"),
            voucher_xml("Journal", "JV-0001", [("Profit &amp; Loss A/c", True, "-50000.00"), ("Reserves and Surplus", False, "50000.00")]),
        )
        data = parse_tally_xml_data(xml)
        self.assertEqual(data.closing_balance("Profit & Loss A/c"), Decimal("-75000.00"))


class AbsentOpeningBalanceTests(unittest.TestCase):
    """Covers a real client bug (2026-08-10), related to but distinct from
    EmptyParentLedgerTests above: the real "Profit & Loss A/c" ledger has
    NO <OPENINGBALANCE> element at all -- the tag is completely absent, not
    just empty (unlike <PARENT/>, which IS present but empty). Confirmed as
    a general Tally pattern (omitting a field entirely for its zero/default
    value), not unique to this one ledger -- see
    _optional_decimal_element in tally_xml_parser.py."""

    def test_ledger_with_absent_openingbalance_defaults_to_zero(self):
        xml = ENVELOPE_OPEN + b"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="Some Nominal Ledger" ACTION="Create">
            <PARENT>Indirect Expenses</PARENT>
          </LEDGER>
        </TALLYMESSAGE>
        """ + ENVELOPE_CLOSE
        data = parse_tally_xml_data(xml)
        self.assertEqual(data.ledgers["Some Nominal Ledger"].opening_balance, Decimal("0.00"))

    def test_ledger_with_present_but_empty_openingbalance_still_raises(self):
        # Distinct from absence -- an EMPTY (but present) OPENINGBALANCE
        # tag isn't the confirmed real shape and stays an error, unlike the
        # PARENT case where present-but-empty is the legitimate one.
        xml = ENVELOPE_OPEN + b"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="Some Ledger" ACTION="Create">
            <PARENT>Indirect Expenses</PARENT>
            <OPENINGBALANCE></OPENINGBALANCE>
          </LEDGER>
        </TALLYMESSAGE>
        """ + ENVELOPE_CLOSE
        with self.assertRaises(TallyXmlParseError) as ctx:
            parse_tally_xml_data(xml)
        self.assertIn("missing required <OPENINGBALANCE>", str(ctx.exception))

    def test_ledger_with_unparseable_openingbalance_still_raises(self):
        # A PRESENT OPENINGBALANCE with garbage content is still a real
        # parse error -- only complete absence defaults to zero.
        xml = build_xml(ledger_xml("Some Ledger", "Indirect Expenses", "not-a-number"))
        with self.assertRaises(TallyXmlParseError) as ctx:
            parse_tally_xml_data(xml)
        self.assertIn("could not parse", str(ctx.exception))

    def test_profit_and_loss_ledger_exact_real_shape_parses_correctly(self):
        # The exact real-world shape: <PARENT/> (empty, present) AND
        # <OPENINGBALANCE> (completely absent), together on the same
        # ledger -- both fixes must hold at once.
        xml = ENVELOPE_OPEN + b"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="Profit &amp; Loss A/c" ACTION="Create">
            <PARENT/>
          </LEDGER>
        </TALLYMESSAGE>
        """ + ENVELOPE_CLOSE
        data = parse_tally_xml_data(xml)
        pl = data.ledgers["Profit & Loss A/c"]
        self.assertEqual(pl.parent, "")
        self.assertEqual(pl.opening_balance, Decimal("0.00"))

    def test_absent_openingbalance_default_is_not_special_cased_to_one_ledger_name(self):
        # The fix must apply to ANY ledger, not just ones named
        # "Profit & Loss A/c" -- the same omission could plausibly appear
        # on any ledger with a genuinely zero opening balance.
        xml = ENVELOPE_OPEN + b"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="Freight Suspense" ACTION="Create">
            <PARENT>Indirect Expenses</PARENT>
          </LEDGER>
        </TALLYMESSAGE>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="Rounding Off" ACTION="Create">
            <PARENT>Indirect Expenses</PARENT>
            <OPENINGBALANCE>0.00</OPENINGBALANCE>
          </LEDGER>
        </TALLYMESSAGE>
        """ + ENVELOPE_CLOSE
        data = parse_tally_xml_data(xml)
        self.assertEqual(data.ledgers["Freight Suspense"].opening_balance, Decimal("0.00"))
        self.assertEqual(data.ledgers["Rounding Off"].opening_balance, Decimal("0.00"))


class TallyDataStructureTests(unittest.TestCase):
    def test_voucher_fields_preserved(self):
        xml = build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"),
            ledger_xml("Sales Account", "Sales Accounts", "0.00"),
            voucher_xml("Sales", "SI-0007", [("HDFC Bank", True, "-2500.00"), ("Sales Account", False, "2500.00")]),
        )
        data = parse_tally_xml_data(xml)
        self.assertEqual(len(data.vouchers), 1)
        v = data.vouchers[0]
        self.assertEqual(v.voucher_number, "SI-0007")
        self.assertEqual(v.vch_type, "Sales")
        self.assertEqual(v.date, "2025-04-01")  # DATE 20250401 -> ISO
        self.assertEqual(v.narration, "Test voucher.")
        self.assertEqual(len(v.legs), 2)

    def test_pl_ledgers_included_in_raw_data_unlike_trial_balance(self):
        xml = build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"),
            ledger_xml("Sales Account", "Sales Accounts", "0.00"),
        )
        data = parse_tally_xml_data(xml)
        self.assertIn("Sales Account", data.ledgers)

    def test_closing_balance_and_vouchers_touching(self):
        xml = build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"),
            ledger_xml("Sales Account", "Sales Accounts", "0.00"),
            voucher_xml("Sales", "SI-0001", [("HDFC Bank", True, "-500.00"), ("Sales Account", False, "500.00")]),
        )
        data = parse_tally_xml_data(xml)
        self.assertEqual(data.closing_balance("HDFC Bank"), Decimal("1500.00"))
        touching = data.vouchers_touching("HDFC Bank")
        self.assertEqual(len(touching), 1)
        self.assertEqual(touching[0].voucher_number, "SI-0001")
        self.assertEqual(data.vouchers_touching("Sales Account")[0].voucher_number, "SI-0001")


class GroupHierarchyResolutionTests(unittest.TestCase):
    """Covers TallyData.resolve_top_level_group and the parse_tally_xml
    P&L-filtering fix that uses it (2026-08-05) -- a ledger filed under a
    company-created custom sub-group (nested under a standard Tally primary
    group, possibly several levels deep) should resolve to that primary
    group, not get stuck on its own immediate PARENT string."""

    def test_ledger_under_primary_group_with_no_groups_at_all(self):
        # Regression case: matches every real sample this project has seen
        # so far (zero <GROUP> masters, every ledger's PARENT already a
        # primary group) -- resolution must be a no-op in this case.
        xml = build_xml(ledger_xml("Axis Bank CC A/c", "Bank Accounts", "1000.00"))
        data = parse_tally_xml_data(xml)
        self.assertEqual(data.resolve_top_level_group("Axis Bank CC A/c"), "Bank Accounts")

    def test_ledger_under_custom_subgroup_resolves_to_primary_group(self):
        # "Overseas Debtors" is a custom group a company created, nested
        # directly under the standard "Sundry Debtors" primary group.
        xml = build_xml(
            group_xml("Overseas Debtors", "Sundry Debtors"),
            ledger_xml("Meridian Global Trading LLC", "Overseas Debtors", "500000.00"),
        )
        data = parse_tally_xml_data(xml)
        self.assertIn("Overseas Debtors", data.groups)
        self.assertEqual(data.groups["Overseas Debtors"].parent, "Sundry Debtors")
        self.assertEqual(data.resolve_top_level_group("Meridian Global Trading LLC"), "Sundry Debtors")

    def test_multi_level_custom_group_chain_resolves_to_primary_group(self):
        # Two custom levels deep: ledger -> "APAC Overseas Debtors" ->
        # "Overseas Debtors" -> "Sundry Debtors" (primary, no GROUP master).
        xml = build_xml(
            group_xml("Overseas Debtors", "Sundry Debtors"),
            group_xml("APAC Overseas Debtors", "Overseas Debtors"),
            ledger_xml("Meridian Global Trading LLC", "APAC Overseas Debtors", "500000.00"),
        )
        data = parse_tally_xml_data(xml)
        self.assertEqual(data.resolve_top_level_group("Meridian Global Trading LLC"), "Sundry Debtors")

    def test_pl_filtering_excludes_ledger_under_custom_subgroup_of_pl_group(self):
        # The actual bug this fixes: without hierarchy resolution, a ledger
        # under a custom sub-group of a P&L primary group would incorrectly
        # survive parse_tally_xml's balance-sheet filtering (its immediate
        # PARENT, "Domestic Sales", doesn't literally match
        # PROFIT_AND_LOSS_PARENT_GROUPS even though it structurally is one).
        xml = build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"),
            group_xml("Domestic Sales", "Sales Accounts"),
            ledger_xml("Sales - Domestic", "Domestic Sales", "0.00"),
        )
        tb = parse_tally_xml(xml)
        self.assertEqual([l.name for l in tb.ledgers], ["HDFC Bank"])

    def test_cyclical_group_chain_does_not_infinite_loop(self):
        # Not a real Tally export (Tally itself doesn't allow creating a
        # cycle), but corrupt/hand-edited input shouldn't hang the parser.
        xml = build_xml(
            group_xml("Group A", "Group B"),
            group_xml("Group B", "Group A"),
            ledger_xml("Some Ledger", "Group A", "0.00"),
        )
        data = parse_tally_xml_data(xml)
        # Just needs to terminate and return *something* -- which specific
        # node it stops on isn't the contract, only that it doesn't hang.
        result = data.resolve_top_level_group("Some Ledger")
        self.assertIn(result, ("Group A", "Group B"))

    def test_identical_duplicate_group_master_is_deduped_not_rejected(self):
        # Real files emit the same group master more than once with an
        # identical PARENT (see CLAUDE.md "Real-world structural robustness
        # pass"). This must dedupe silently, not raise.
        xml = build_xml(
            group_xml("Overseas Debtors", "Sundry Debtors"),
            group_xml("Overseas Debtors", "Sundry Debtors"),
            ledger_xml("Some Ledger", "Overseas Debtors", "0.00"),
        )
        data = parse_tally_xml_data(xml)
        self.assertEqual(len(data.groups), 1)
        self.assertEqual(data.resolve_top_level_group("Some Ledger"), "Sundry Debtors")

    def test_conflicting_duplicate_group_master_raises(self):
        # Same NAME, DIFFERENT non-empty parent -> a genuine conflict.
        xml = build_xml(
            group_xml("Overseas Debtors", "Sundry Debtors"),
            group_xml("Overseas Debtors", "Sundry Creditors"),
            ledger_xml("Some Ledger", "Overseas Debtors", "0.00"),
        )
        with self.assertRaises(TallyXmlParseError) as ctx:
            parse_tally_xml_data(xml)
        self.assertIn("conflicting parents", str(ctx.exception))

    def test_builtin_primary_group_emitted_as_master_resolves_correctly(self):
        # Real Tally emits <GROUP> masters for its own built-in primaries,
        # with the top primary carrying an EMPTY parent. The resolve walk must
        # stop at the primary, not step into "" (which would break P&L
        # filtering). Chain: ledger -> custom vendor group -> Sundry Creditors
        # -> Current Liabilities (empty parent).
        xml = build_xml(
            group_xml("Current Liabilities", ""),
            group_xml("Sundry Creditors", "Current Liabilities"),
            group_xml("Food Vendors", "Sundry Creditors"),
            ledger_xml("A Vendor", "Food Vendors", "1000.00"),
        )
        data = parse_tally_xml_data(xml)
        self.assertEqual(data.resolve_top_level_group("A Vendor"), "Current Liabilities")

    def test_builtin_pl_group_as_master_still_excluded_from_trial_balance(self):
        # A Sales ledger under "Sales Accounts" emitted as a master (empty
        # parent) must still resolve to "Sales Accounts" and be excluded from
        # the balance-sheet TrialBalance -- the walk must not overshoot to "".
        xml = build_xml(
            group_xml("Sales Accounts", ""),
            ledger_xml("Permanent BS Ledger", "Current Assets", "5000.00"),
            ledger_xml("Food Sales", "Sales Accounts", "0.00"),
        )
        data = parse_tally_xml_data(xml)
        self.assertEqual(data.resolve_top_level_group("Food Sales"), "Sales Accounts")
        tb = parse_tally_xml(xml)
        names = [l.name for l in tb.ledgers]
        self.assertIn("Permanent BS Ledger", names)
        self.assertNotIn("Food Sales", names)

    def test_group_with_no_name_raises(self):
        xml = build_xml(b"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <GROUP ACTION="Create">
            <PARENT>Sundry Debtors</PARENT>
          </GROUP>
        </TALLYMESSAGE>
        """, ledger_xml("Some Ledger", "Sundry Debtors", "0.00"))
        with self.assertRaises(TallyXmlParseError) as ctx:
            parse_tally_xml_data(xml)
        self.assertIn("NAME attribute", str(ctx.exception))


class EncodingTests(unittest.TestCase):
    """Covers _normalize_xml_encoding -- real Tally exports are commonly
    UTF-16 with a BOM, not UTF-8 (confirmed against real user files,
    2026-08-06), and can contain characters XML 1.0 doesn't allow at all,
    either as a numeric character reference (e.g. &#4;) or, distinctly,
    embedded raw/literally in the decoded text (e.g. a bare 0x05 byte inside
    a <STATKEY> field, confirmed against real user files 2026-08-07)."""

    def test_utf16_le_with_bom_parses_correctly(self):
        xml = build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"),
            ledger_xml("Sales Account", "Sales Accounts", "0.00"),
            voucher_xml("Sales", "SI-0007", [("HDFC Bank", True, "-2500.00"), ("Sales Account", False, "2500.00")]),
        )
        utf16_xml = to_utf16(xml, "le")
        self.assertTrue(utf16_xml.startswith(b"\xff\xfe"))
        data = parse_tally_xml_data(utf16_xml)
        self.assertEqual(data.closing_balance("HDFC Bank"), Decimal("3500.00"))
        self.assertEqual(len(data.vouchers), 1)

    def test_utf16_be_with_bom_parses_correctly(self):
        xml = build_xml(ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"))
        utf16_xml = to_utf16(xml, "be")
        self.assertTrue(utf16_xml.startswith(b"\xfe\xff"))
        data = parse_tally_xml_data(utf16_xml)
        self.assertEqual(data.ledgers["HDFC Bank"].opening_balance, Decimal("1000.00"))

    def test_utf8_with_bom_still_parses(self):
        xml = build_xml(ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"))
        bom_xml = b"\xef\xbb\xbf" + xml
        data = parse_tally_xml_data(bom_xml)
        self.assertIn("HDFC Bank", data.ledgers)

    def test_plain_utf8_without_bom_still_parses(self):
        # Regression: every sample this project had been tested against
        # before 2026-08-05 is plain UTF-8, no BOM at all.
        xml = build_xml(ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"))
        data = parse_tally_xml_data(xml)
        self.assertIn("HDFC Bank", data.ledgers)

    def test_undecodable_bytes_raise_clear_error(self):
        garbage = b"\x80\x81\x82\x83not valid utf-8 or utf-16"
        with self.assertRaises(TallyXmlParseError) as ctx:
            parse_tally_xml_data(garbage)
        self.assertIn("UTF-8 or UTF-16", str(ctx.exception))

    def test_strip_invalid_numeric_entities_removes_control_char_refs(self):
        # &#4; is a real example observed in real Tally exports (End of
        # Transmission, a C0 control character) -- not valid XML 1.0 at all.
        text = 'Narration with a stray control char &#4; in the middle.'
        cleaned = _strip_invalid_numeric_entities(text)
        self.assertEqual(cleaned, 'Narration with a stray control char  in the middle.')

    def test_strip_invalid_numeric_entities_preserves_valid_references(self):
        # &#8377; is the Rupee sign (₹) -- a perfectly valid XML character
        # reference that must survive untouched.
        text = 'Amount: &#8377;1,000'
        self.assertEqual(_strip_invalid_numeric_entities(text), text)

    def test_strip_raw_illegal_control_chars_removes_raw_control_char(self):
        # Confirmed against real user files (2026-08-07): a <STATKEY> field
        # containing a raw (not entity-encoded) ASCII 0x05 -- Tally's own
        # internal delimiter joining several values into one field.
        text = "2023\x05376\x05Outward Invoice\x05S1.4.2023"
        self.assertEqual(_strip_raw_illegal_control_chars(text), "2023376Outward InvoiceS1.4.2023")

    def test_strip_raw_illegal_control_chars_covers_full_illegal_range_not_just_0x05(self):
        # The fix must not be hardcoded to the one control character
        # actually observed -- nothing guarantees Tally only ever emits
        # 0x05 raw; cover a spread across the whole XML 1.0 illegal range
        # (see _is_valid_xml_char).
        text = "A\x00B\x01C\x08D\x0bE\x0cF\x0eG\x1fH"
        self.assertEqual(_strip_raw_illegal_control_chars(text), "ABCDEFGH")

    def test_strip_raw_illegal_control_chars_preserves_valid_whitespace_and_text(self):
        # Tab, newline, and carriage return ARE valid XML 1.0 characters
        # (see _is_valid_xml_char) despite being control characters -- must
        # not be stripped alongside the illegal ones.
        text = "Line one\twith a tab\nLine two\r\nNormal punctuation: ₹1,000.00 (50%)."
        self.assertEqual(_strip_raw_illegal_control_chars(text), text)

    def test_invalid_numeric_entity_in_narration_does_not_break_parsing(self):
        xml = ENVELOPE_OPEN + ledger_xml("HDFC Bank", "Bank Accounts", "1000.00") + ledger_xml(
            "Sales Account", "Sales Accounts", "0.00"
        ) + b"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Sales" ACTION="Create">
            <DATE>20250401</DATE>
            <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
            <VOUCHERNUMBER>SI-0001</VOUCHERNUMBER>
            <NARRATION>Invoice with stray control char &#4; embedded.</NARRATION>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>HDFC Bank</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-500.00</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>Sales Account</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>500.00</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
          </VOUCHER>
        </TALLYMESSAGE>
        """ + ENVELOPE_CLOSE
        data = parse_tally_xml_data(xml)
        self.assertEqual(data.vouchers[0].narration, "Invoice with stray control char  embedded.")

    def test_raw_control_char_in_statkey_does_not_break_parsing(self):
        # Regression for the real failure (2026-08-07): unlike &#4; above,
        # this control character is embedded LITERALLY in the file's bytes,
        # not spelled out as a numeric entity reference -- expat rejects it
        # as "not well-formed" exactly the same either way, so it must be
        # stripped as raw text too, not just when it appears as an entity.
        xml = ENVELOPE_OPEN + ledger_xml("HDFC Bank", "Bank Accounts", "1000.00") + ledger_xml(
            "Sales Account", "Sales Accounts", "0.00"
        ) + """
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Sales" ACTION="Create">
            <DATE>20250401</DATE>
            <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
            <VOUCHERNUMBER>SI-0001</VOUCHERNUMBER>
            <NARRATION>Test voucher.</NARRATION>
            <STATKEY>2023\x05376\x05Outward Invoice\x05S1.4.2023</STATKEY>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>HDFC Bank</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-500.00</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>Sales Account</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>500.00</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
          </VOUCHER>
        </TALLYMESSAGE>
        """.encode() + ENVELOPE_CLOSE
        data = parse_tally_xml_data(xml)
        self.assertEqual(len(data.vouchers), 1)
        self.assertEqual(data.vouchers[0].voucher_number, "SI-0001")
        self.assertEqual(data.vouchers[0].narration, "Test voucher.")
        self.assertEqual(data.closing_balance("HDFC Bank"), Decimal("1500.00"))

    def test_stale_encoding_declaration_after_reencoding_does_not_break_expat(self):
        # After decoding UTF-16 and re-encoding to UTF-8, the original
        # <?xml ... encoding="UTF-16"?> declaration would describe the wrong
        # encoding for the bytes now being handed to ElementTree if left in
        # place -- _normalize_xml_encoding must strip it.
        xml = build_xml(ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"))
        normalized = _normalize_xml_encoding(to_utf16(xml, "le"))
        self.assertNotIn(b"encoding=", normalized[:60])


class SplitMastersTransactionsTests(unittest.TestCase):
    """Covers parse_tally_xml_fragment / merge_tally_xml_fragments /
    parse_tally_xml_data_multi -- confirmed against real user files
    (2026-08-06) that Tally commonly exports masters (<GROUP>/<LEDGER>) and
    transactions (<VOUCHER>) as two SEPARATE files rather than one combined
    file, and both can carry the same <REPORTNAME> regardless of which kind
    of content is actually inside."""

    def _masters_only_file(self):
        return build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"),
            ledger_xml("Sales Account", "Sales Accounts", "0.00"),
        )

    def _transactions_only_file(self):
        # Same <REPORTNAME> as a masters file would use -- deliberately, to
        # prove REPORTNAME is never consulted (see build_xml/ENVELOPE_OPEN,
        # which doesn't even vary REPORTNAME per file in this test suite).
        return build_xml(
            voucher_xml("Sales", "SI-0001", [("HDFC Bank", True, "-500.00"), ("Sales Account", False, "500.00")]),
        )

    def test_transactions_only_fragment_has_no_ledgers(self):
        # This is exactly the case parse_tally_xml_data (single-file) can't
        # handle -- it would reject this file outright for having zero
        # <LEDGER> masters.
        fragment = parse_tally_xml_fragment(self._transactions_only_file())
        self.assertEqual(fragment.ledgers, {})
        self.assertEqual(len(fragment.vouchers), 1)

    def test_masters_only_fragment_has_no_vouchers(self):
        fragment = parse_tally_xml_fragment(self._masters_only_file())
        self.assertEqual(len(fragment.ledgers), 2)
        self.assertEqual(fragment.vouchers, [])

    def test_merge_combines_masters_and_transactions_files(self):
        masters = parse_tally_xml_fragment(self._masters_only_file())
        transactions = parse_tally_xml_fragment(self._transactions_only_file())
        merged = merge_tally_xml_fragments([masters, transactions])
        self.assertEqual(set(merged.ledgers), {"HDFC Bank", "Sales Account"})
        self.assertEqual(len(merged.vouchers), 1)
        self.assertEqual(merged.closing_balance("HDFC Bank"), Decimal("1500.00"))

    def test_merge_order_does_not_matter(self):
        masters = parse_tally_xml_fragment(self._masters_only_file())
        transactions = parse_tally_xml_fragment(self._transactions_only_file())
        merged = merge_tally_xml_fragments([transactions, masters])
        self.assertEqual(merged.closing_balance("HDFC Bank"), Decimal("1500.00"))

    def test_parse_tally_xml_data_multi_end_to_end(self):
        data = parse_tally_xml_data_multi([self._transactions_only_file(), self._masters_only_file()])
        self.assertEqual(set(data.ledgers), {"HDFC Bank", "Sales Account"})
        self.assertEqual(len(data.vouchers), 1)

    def test_single_combined_file_via_multi_still_works(self):
        # A single all-in-one file is just a one-fragment merge (a no-op).
        combined = build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"),
            ledger_xml("Sales Account", "Sales Accounts", "0.00"),
            voucher_xml("Sales", "SI-0001", [("HDFC Bank", True, "-500.00"), ("Sales Account", False, "500.00")]),
        )
        data = parse_tally_xml_data_multi([combined])
        self.assertEqual(len(data.ledgers), 2)
        self.assertEqual(len(data.vouchers), 1)

    def test_voucher_referencing_ledger_in_sibling_fragment_is_not_an_error(self):
        # The exact bug this feature fixes: without merge-time validation,
        # this voucher's ledger references would look unresolvable from
        # either file parsed alone.
        merged = parse_tally_xml_data_multi([self._masters_only_file(), self._transactions_only_file()])
        touching = merged.vouchers_touching("HDFC Bank")
        self.assertEqual(len(touching), 1)

    def test_voucher_referencing_truly_unknown_ledger_across_all_files_raises(self):
        transactions = build_xml(
            voucher_xml("Sales", "SI-0001", [("HDFC Bank", True, "-500.00"), ("Nonexistent Ledger", False, "500.00")]),
        )
        with self.assertRaises(TallyXmlParseError) as ctx:
            parse_tally_xml_data_multi([self._masters_only_file(), transactions])
        self.assertIn("no matching <LEDGER> master in any of the uploaded files", str(ctx.exception))

    def test_no_ledgers_in_any_file_raises(self):
        with self.assertRaises(TallyXmlParseError) as ctx:
            parse_tally_xml_data_multi([self._transactions_only_file()])
        self.assertIn("No <LEDGER> master elements found in any", str(ctx.exception))

    def test_overlapping_ledger_across_files_deduped_when_one_omits_opening_balance(self):
        # The real case (CLAUDE.md "Real-world structural robustness pass"): a
        # transactions file embeds its own copy of a master that the masters
        # file also has, with one file stating an opening balance the other
        # omits. Merge must dedupe and keep the stated balance -- regardless
        # of file order -- not reject.
        with_ob = build_xml(ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"))
        without_ob = ENVELOPE_OPEN + b"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="HDFC Bank" ACTION="Create"><PARENT>Bank Accounts</PARENT></LEDGER>
        </TALLYMESSAGE>
        """ + ENVELOPE_CLOSE
        for order in ([with_ob, without_ob], [without_ob, with_ob]):
            data = parse_tally_xml_data_multi(order)
            self.assertEqual(len(data.ledgers), 1)
            self.assertEqual(data.ledgers["HDFC Bank"].opening_balance, Decimal("1000.00"))

    def test_conflicting_ledger_opening_balance_across_files_prefers_masters_file(self):
        # Two DIFFERENT non-zero opening balances for the same ledger across a
        # masters file and a transactions file is a legitimate snapshot
        # difference (real: a bank ledger had different opening balances in the
        # two files), NOT corruption -- so it must NOT raise. The dedicated
        # masters file (more ledger masters) wins, regardless of upload order.
        masters_file = build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"),
            ledger_xml("Other Ledger", "Current Assets", "50.00"),
        )
        trnx_file = build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "2000.00"),
            voucher_xml("Contra", "C-1", [("HDFC Bank", True, "-10.00"), ("Other Ledger", False, "10.00")]),
        )
        for order in ([masters_file, trnx_file], [trnx_file, masters_file]):
            data = parse_tally_xml_data_multi(order)
            self.assertEqual(data.ledgers["HDFC Bank"].opening_balance, Decimal("1000.00"),
                             "masters-file opening balance should win regardless of order")

    def test_identical_group_across_files_deduped_not_rejected(self):
        file_a = build_xml(group_xml("Overseas Debtors", "Sundry Debtors"), ledger_xml("A", "Overseas Debtors", "0.00"))
        file_b = build_xml(group_xml("Overseas Debtors", "Sundry Debtors"), ledger_xml("B", "Overseas Debtors", "0.00"))
        data = parse_tally_xml_data_multi([file_a, file_b])
        self.assertEqual(len(data.groups), 1)
        self.assertEqual(set(data.ledgers), {"A", "B"})

    def test_conflicting_group_across_files_resolves_to_masters_file(self):
        # A group re-parented between a masters file and a transactions file is
        # tolerated across files (unlike within one file). The masters-heavier
        # file wins; it must not raise.
        masters_file = build_xml(
            group_xml("Overseas Debtors", "Sundry Debtors"),
            ledger_xml("A", "Overseas Debtors", "0.00"),
            ledger_xml("B", "Sundry Debtors", "0.00"),
        )
        trnx_file = build_xml(
            group_xml("Overseas Debtors", "Sundry Creditors"),
            voucher_xml("Journal", "J-1", [("A", True, "-5.00"), ("B", False, "5.00")]),
        )
        for order in ([masters_file, trnx_file], [trnx_file, masters_file]):
            data = parse_tally_xml_data_multi(order)
            self.assertEqual(data.groups["Overseas Debtors"].parent, "Sundry Debtors")

    def test_no_files_raises(self):
        with self.assertRaises(TallyXmlParseError):
            parse_tally_xml_data_multi([])

    def test_split_files_can_also_be_utf16(self):
        # The two real-world problems compound: a split export can also be
        # UTF-16 encoded.
        merged = parse_tally_xml_data_multi([
            to_utf16(self._masters_only_file(), "le"),
            to_utf16(self._transactions_only_file(), "be"),
        ])
        self.assertEqual(merged.closing_balance("HDFC Bank"), Decimal("1500.00"))


class RejectionTests(unittest.TestCase):
    def test_not_well_formed_raises(self):
        with self.assertRaises(TallyXmlParseError):
            parse_tally_xml(b"<ENVELOPE><UNCLOSED>")

    def test_wrong_root_element_raises(self):
        with self.assertRaises(TallyXmlParseError):
            parse_tally_xml(b'<?xml version="1.0"?><NOTENVELOPE></NOTENVELOPE>')

    def test_no_ledgers_raises(self):
        xml = ENVELOPE_OPEN + ENVELOPE_CLOSE
        with self.assertRaises(TallyXmlParseError):
            parse_tally_xml(xml)

    def test_conflicting_duplicate_ledger_name_raises(self):
        # Same NAME in one file with two DIFFERENT non-zero opening balances
        # is a genuine conflict (an identical/complementary duplicate would
        # instead dedupe -- see the SplitMastersTransactions dedup tests).
        xml = build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"),
            ledger_xml("HDFC Bank", "Bank Accounts", "2000.00"),
        )
        with self.assertRaises(TallyXmlParseError) as ctx:
            parse_tally_xml(xml)
        self.assertIn("conflicting opening balances", str(ctx.exception))

    def test_identical_duplicate_ledger_name_in_one_file_is_deduped(self):
        xml = build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"),
            ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"),
            ledger_xml("Sales Account", "Sales Accounts", "0.00"),
            voucher_xml("Sales", "SI-1", [("HDFC Bank", True, "-500.00"), ("Sales Account", False, "500.00")]),
        )
        data = parse_tally_xml_data(xml)
        self.assertEqual(data.ledgers["HDFC Bank"].opening_balance, Decimal("1000.00"))

    def test_voucher_with_one_leg_raises(self):
        xml = build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"),
            voucher_xml("Journal", "JV-0001", [("HDFC Bank", True, "-500.00")]),
        )
        with self.assertRaises(TallyXmlParseError) as ctx:
            parse_tally_xml(xml)
        self.assertIn("fewer than 2", str(ctx.exception))

    def test_voucher_legs_not_summing_to_zero_raises(self):
        xml = build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"),
            ledger_xml("Sales Account", "Sales Accounts", "0.00"),
            voucher_xml("Receipt", "RCT-0001", [("HDFC Bank", True, "-500.00"), ("Sales Account", False, "400.00")]),
        )
        with self.assertRaises(TallyXmlParseError) as ctx:
            parse_tally_xml(xml)
        self.assertIn("do not sum to zero", str(ctx.exception))

    def test_sign_disagreement_is_tolerated_when_voucher_still_balances(self):
        # ISDEEMEDPOSITIVE vs AMOUNT sign disagreement is NOT rejected anymore
        # (real "Rounding Off" legs legitimately disagree -- found 2026-08-08).
        # The signed AMOUNT is authoritative and the sum-to-zero check is the
        # real integrity guard: this voucher's legs still net to zero, so it
        # parses, and balances are computed from AMOUNT.
        xml = build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"),
            ledger_xml("Sales Account", "Sales Accounts", "0.00"),
            voucher_xml("Receipt", "RCT-0001", [("HDFC Bank", True, "500.00"), ("Sales Account", False, "-500.00")]),
        )
        data = parse_tally_xml_data(xml)
        self.assertEqual(len(data.vouchers), 1)
        self.assertEqual(data.closing_balance("HDFC Bank"), Decimal("500.00"))

    def test_real_rounding_off_entry_credit_marked_but_negative_amount(self):
        # The exact real shape: a credit-marked (No) rounding leg with a small
        # negative amount, balanced by the rest of the voucher.
        xml = build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"),
            ledger_xml("A Vendor", "Sundry Creditors", "0.00"),
            ledger_xml("Rounding Off", "Indirect Expenses", "0.00"),
            voucher_xml("Payment", "PV-1", [
                ("A Vendor", True, "-100.20"),
                ("Rounding Off", False, "-0.20"),   # credit-marked, negative amount
                ("HDFC Bank", False, "100.40"),
            ]),
        )
        data = parse_tally_xml_data(xml)
        self.assertEqual(len(data.vouchers), 1)
        self.assertEqual(data.closing_balance("Rounding Off"), Decimal("0.20"))

    def test_voucher_referencing_unknown_ledger_raises(self):
        xml = build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"),
            voucher_xml("Receipt", "RCT-0001", [("HDFC Bank", True, "-500.00"), ("Nonexistent Ledger", False, "500.00")]),
        )
        with self.assertRaises(TallyXmlParseError) as ctx:
            parse_tally_xml(xml)
        self.assertIn("no matching", str(ctx.exception))

    def test_invalid_isdeemedpositive_value_raises(self):
        xml = ENVELOPE_OPEN + ledger_xml("HDFC Bank", "Bank Accounts", "1000.00") + ledger_xml("Sales Account", "Sales Accounts", "0.00") + b"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Receipt" ACTION="Create">
            <DATE>20250401</DATE>
            <VOUCHERTYPENAME>Receipt</VOUCHERTYPENAME>
            <VOUCHERNUMBER>RCT-0001</VOUCHERNUMBER>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>HDFC Bank</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Maybe</ISDEEMEDPOSITIVE>
              <AMOUNT>-500.00</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>Sales Account</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>500.00</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
          </VOUCHER>
        </TALLYMESSAGE>
        """ + ENVELOPE_CLOSE
        with self.assertRaises(TallyXmlParseError) as ctx:
            parse_tally_xml(xml)
        self.assertIn("ISDEEMEDPOSITIVE", str(ctx.exception))


class ErrorSubclassTests(unittest.TestCase):
    """Covers the TallyXmlParseError subclass hierarchy (added 2026-08-08) --
    api.py's error classification (see parse_error_classification.py)
    depends on the SPECIFIC subclass raised, not just on TallyXmlParseError
    generally, to turn a failure into a plain-language, user-facing message.
    A test here failing means a failure that should be specifically
    recognized (e.g. a truncated file) would silently fall back to the
    generic "something's wrong with this file's data" bucket instead."""

    def test_truncated_file_raises_truncated_error_specifically(self):
        # Confirmed against a real client file (2026-08-08): a large export
        # cut off mid-transfer raises exactly this shape -- content that
        # runs out before the XML structure is complete, not a malformed
        # token. Deep truncation (mid-way through a large document, not at
        # the very start) to match the real reported shape.
        xml = build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"),
            ledger_xml("Sales Account", "Sales Accounts", "0.00"),
        )
        truncated = xml[: len(xml) - 40]  # cut off before ENVELOPE_CLOSE
        with self.assertRaises(TallyXmlTruncatedError):
            parse_tally_xml_data(truncated)

    def test_empty_file_also_raises_truncated_error(self):
        # Same expat error code (XML_ERROR_NO_ELEMENTS) as a mid-document
        # cut-off -- both mean "never reached a complete document".
        with self.assertRaises(TallyXmlTruncatedError):
            parse_tally_xml_data(b"")

    def test_genuinely_malformed_xml_raises_malformed_error_not_truncated(self):
        # An actual bad token, not a premature end-of-input -- must NOT be
        # classified as truncation.
        with self.assertRaises(TallyXmlMalformedError) as ctx:
            parse_tally_xml_data(b"<ENVELOPE><BODY>&undefined_entity;</BODY></ENVELOPE>")
        self.assertNotIsInstance(ctx.exception, TallyXmlTruncatedError)

    def test_wrong_root_element_raises_not_a_tally_export_error(self):
        with self.assertRaises(TallyXmlNotATallyExportError):
            parse_tally_xml_data(b'<?xml version="1.0"?><NOTENVELOPE></NOTENVELOPE>')

    def test_no_ledgers_raises_not_a_tally_export_error(self):
        xml = ENVELOPE_OPEN + ENVELOPE_CLOSE
        with self.assertRaises(TallyXmlNotATallyExportError):
            parse_tally_xml_data(xml)

    def test_no_ledgers_across_split_files_raises_not_a_tally_export_error(self):
        with self.assertRaises(TallyXmlNotATallyExportError):
            parse_tally_xml_data_multi([ENVELOPE_OPEN + ENVELOPE_CLOSE])

    def test_undecodable_bytes_raise_encoding_error_specifically(self):
        garbage = b"\x80\x81\x82\x83not valid utf-8 or utf-16"
        with self.assertRaises(TallyXmlEncodingError):
            parse_tally_xml_data(garbage)

    def test_duplicate_ledger_raises_base_class_not_a_named_subclass(self):
        # A recognized DATA problem (not structural/encoding) -- deliberately
        # stays the base TallyXmlParseError, which api.py's classifier maps
        # to the generic "file_data_issue" category, not one of the more
        # specific ones above.
        xml = build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"),
            ledger_xml("HDFC Bank", "Bank Accounts", "2000.00"),
        )
        try:
            parse_tally_xml_data(xml)
            self.fail("expected TallyXmlParseError")
        except TallyXmlParseError as e:
            self.assertNotIsInstance(e, TallyXmlTruncatedError)
            self.assertNotIsInstance(e, TallyXmlMalformedError)
            self.assertNotIsInstance(e, TallyXmlNotATallyExportError)
            self.assertNotIsInstance(e, TallyXmlEncodingError)


def invoice_voucher_xml(vch_type, vn, legs):
    """Invoice-mode voucher: uses <LEDGERENTRIES.LIST> (Purchase/Sales) rather
    than <ALLLEDGERENTRIES.LIST>, and carries a parallel (ignored)
    <ALLINVENTORYENTRIES.LIST> -- the real shape found 2026-08-08."""
    entries = "".join(f"""
        <LEDGERENTRIES.LIST>
          <LEDGERNAME>{name}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>{"Yes" if is_debit else "No"}</ISDEEMEDPOSITIVE>
          <AMOUNT>{amount}</AMOUNT>
        </LEDGERENTRIES.LIST>
    """ for name, is_debit, amount in legs)
    return f"""
    <TALLYMESSAGE xmlns:UDF="TallyUDF">
      <VOUCHER VCHTYPE="{vch_type}" ACTION="Create">
        <DATE>20250401</DATE>
        <VOUCHERNUMBER>{vn}</VOUCHERNUMBER>
        <ALLINVENTORYENTRIES.LIST>
          <STOCKITEMNAME>Some Item</STOCKITEMNAME>
          <AMOUNT>1000.00</AMOUNT>
        </ALLINVENTORYENTRIES.LIST>
        {entries}
      </VOUCHER>
    </TALLYMESSAGE>
    """.encode()


class RealWorldStructuralPatternsTests(unittest.TestCase):
    """Patterns found only against real (anonymized) Tally exports, 2026-08-08
    -- see CLAUDE.md "Real-world structural robustness pass"."""

    def _masters(self):
        return (
            ledger_xml("A Vendor", "Sundry Creditors", "0.00")
            + ledger_xml("Food Purchases", "Purchase Accounts", "0.00")
            + ledger_xml("HDFC Bank", "Bank Accounts", "1000.00")
        )

    def test_invoice_mode_ledgerentries_list_is_parsed(self):
        # Previously raised "fewer than 2 ledger entries" because only
        # ALLLEDGERENTRIES.LIST was read.
        xml = build_xml(
            self._masters(),
            invoice_voucher_xml("Purchase", "PI-1",
                                 [("Food Purchases", True, "-1000.00"), ("A Vendor", False, "1000.00")]),
        )
        data = parse_tally_xml_data(xml)
        self.assertEqual(len(data.vouchers), 1)
        self.assertEqual(len(data.vouchers[0].legs), 2)
        # closing balance of the creditor reflects the credit leg
        self.assertEqual(data.closing_balance("A Vendor"), Decimal("-1000.00"))

    def test_invoice_mode_and_accounting_mode_vouchers_coexist(self):
        xml = build_xml(
            self._masters(),
            invoice_voucher_xml("Purchase", "PI-1",
                                 [("Food Purchases", True, "-1000.00"), ("A Vendor", False, "1000.00")]),
            voucher_xml("Payment", "PV-1",
                        [("A Vendor", True, "-1000.00"), ("HDFC Bank", False, "1000.00")]),
        )
        data = parse_tally_xml_data(xml)
        self.assertEqual(len(data.vouchers), 2)
        # A Vendor: credited 1000 by the purchase, debited 1000 by the payment -> net 0
        self.assertEqual(data.closing_balance("A Vendor"), Decimal("0.00"))

    def _voucher_with_flag(self, flag):
        return f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Journal" ACTION="Create">
            <DATE>20250401</DATE>
            <VOUCHERNUMBER>JV-SKIP</VOUCHERNUMBER>
            <{flag}>Yes</{flag}>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>HDFC Bank</LEDGERNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-1.00</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
          </VOUCHER>
        </TALLYMESSAGE>
        """.encode()

    def test_cancelled_deleted_optional_vouchers_are_skipped(self):
        # Each of these is a single-leg, unbalanced voucher -- if NOT skipped,
        # it would raise "fewer than 2 ledger entries". Skipping proves it's
        # dropped before validation.
        for flag in ("ISCANCELLED", "ISDELETED", "ISOPTIONAL"):
            xml = build_xml(
                self._masters(),
                voucher_xml("Payment", "PV-1",
                            [("A Vendor", True, "-1000.00"), ("HDFC Bank", False, "1000.00")]),
                self._voucher_with_flag(flag),
            )
            data = parse_tally_xml_data(xml)
            self.assertEqual(len(data.vouchers), 1, f"{flag} voucher should be skipped")
            self.assertEqual(data.vouchers[0].voucher_number, "PV-1")

    def test_whitespace_padded_ledger_name_matches_its_voucher_legs(self):
        # Real Tally pads a master's NAME attribute with a trailing space
        # (NAME="Ashok Joshi ") while its <LEDGERNAME> leg references are
        # unpadded ("Ashok Joshi"). The parser must strip NAME so the ledger
        # matches its own legs -- previously raised "no matching <LEDGER>
        # master".
        padded_master = """
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="Ashok Joshi " ACTION="Create">
            <PARENT>Sundry Creditors</PARENT>
            <OPENINGBALANCE>0.00</OPENINGBALANCE>
          </LEDGER>
        </TALLYMESSAGE>
        """.encode()
        xml = build_xml(
            padded_master,
            ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"),
            voucher_xml("Payment", "PV-1",
                        [("Ashok Joshi", True, "-500.00"), ("HDFC Bank", False, "500.00")]),
        )
        data = parse_tally_xml_data(xml)
        # master keyed by the stripped name; leg resolves to it
        self.assertIn("Ashok Joshi", data.ledgers)
        self.assertEqual(data.closing_balance("Ashok Joshi"), Decimal("500.00"))

    def test_whitespace_padded_group_name_matches_child_parent_reference(self):
        xml = build_xml(
            group_xml("Overseas Debtors ", "Sundry Debtors"),  # padded group NAME
            ledger_xml("A Customer", "Overseas Debtors", "100.00"),  # unpadded PARENT ref
        )
        data = parse_tally_xml_data(xml)
        self.assertEqual(data.resolve_top_level_group("A Customer"), "Sundry Debtors")

    def test_leg_amount_uses_direct_child_not_nested_allocation_amount(self):
        # A real ledger entry nests BILLALLOCATIONS.LIST with its own <AMOUNT>.
        # The leg's amount is the DIRECT child (-1000), never a nested one.
        voucher = """
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Payment" ACTION="Create">
            <DATE>20250401</DATE>
            <VOUCHERNUMBER>PV-1</VOUCHERNUMBER>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>A Vendor</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-1000.00</AMOUNT>
              <BILLALLOCATIONS.LIST><NAME>B1</NAME><AMOUNT>-600.00</AMOUNT></BILLALLOCATIONS.LIST>
              <BILLALLOCATIONS.LIST><NAME>B2</NAME><AMOUNT>-400.00</AMOUNT></BILLALLOCATIONS.LIST>
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>HDFC Bank</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>1000.00</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
          </VOUCHER>
        </TALLYMESSAGE>
        """.encode()
        xml = build_xml(self._masters(), voucher)
        data = parse_tally_xml_data(xml)
        vend_leg = data.vouchers[0].leg_for("A Vendor")
        self.assertEqual(vend_leg.amount, Decimal("-1000.00"))
        # closing balance uses the direct-child amount only (0 - (-1000) = 1000)
        self.assertEqual(data.closing_balance("A Vendor"), Decimal("1000.00"))


if __name__ == "__main__":
    unittest.main()
