import unittest
from decimal import Decimal

from tally_xml_parser import TallyXmlParseError, parse_tally_xml, parse_tally_xml_data

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

    def test_duplicate_group_name_raises(self):
        xml = build_xml(
            group_xml("Overseas Debtors", "Sundry Debtors"),
            group_xml("Overseas Debtors", "Sundry Debtors"),
            ledger_xml("Some Ledger", "Overseas Debtors", "0.00"),
        )
        with self.assertRaises(TallyXmlParseError) as ctx:
            parse_tally_xml_data(xml)
        self.assertIn("Duplicate group", str(ctx.exception))

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

    def test_duplicate_ledger_name_raises(self):
        xml = build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"),
            ledger_xml("HDFC Bank", "Bank Accounts", "2000.00"),
        )
        with self.assertRaises(TallyXmlParseError) as ctx:
            parse_tally_xml(xml)
        self.assertIn("Duplicate", str(ctx.exception))

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

    def test_debit_leg_with_positive_amount_raises(self):
        xml = build_xml(
            ledger_xml("HDFC Bank", "Bank Accounts", "1000.00"),
            ledger_xml("Sales Account", "Sales Accounts", "0.00"),
            voucher_xml("Receipt", "RCT-0001", [("HDFC Bank", True, "500.00"), ("Sales Account", False, "-500.00")]),
        )
        with self.assertRaises(TallyXmlParseError) as ctx:
            parse_tally_xml(xml)
        self.assertIn("sign convention", str(ctx.exception))

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


if __name__ == "__main__":
    unittest.main()
