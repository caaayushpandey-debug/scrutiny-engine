import unittest
from decimal import Decimal

from trial_balance_csv_parser import TrialBalanceParseError, parse_trial_balance_csv


class CanonicalFormatTests(unittest.TestCase):
    def test_parses_reference_format(self):
        csv_text = (
            "Ledger Name,Group,Debit,Credit\n"
            "HDFC Bank Current A/c,Bank Accounts,1250000.00,0.00\n"
            "Sundry Creditors - ABC Traders,Sundry Creditors,0.00,340000.00\n"
        )
        tb = parse_trial_balance_csv(csv_text)
        self.assertEqual(len(tb.ledgers), 2)
        self.assertEqual(tb.ledgers[0].name, "HDFC Bank Current A/c")
        self.assertEqual(tb.ledgers[0].group, "Bank Accounts")
        self.assertEqual(tb.ledgers[0].debit, Decimal("1250000.00"))
        self.assertEqual(tb.ledgers[0].credit, Decimal("0.00"))


class HeaderAliasTests(unittest.TestCase):
    def test_alternate_headers_and_order(self):
        # Different names, different order, no Group column at all.
        csv_text = (
            "Dr,Particulars,Cr\n"
            "1250000.00,HDFC Bank Current A/c,0.00\n"
        )
        tb = parse_trial_balance_csv(csv_text)
        self.assertEqual(len(tb.ledgers), 1)
        self.assertEqual(tb.ledgers[0].name, "HDFC Bank Current A/c")
        self.assertEqual(tb.ledgers[0].group, "")
        self.assertEqual(tb.ledgers[0].debit, Decimal("1250000.00"))

    def test_header_case_and_punctuation_insensitive(self):
        csv_text = (
            "LEDGER_NAME,account-group,Debit Amount,Credit Amount\n"
            "Cash,Current Assets,5000.00,0.00\n"
        )
        tb = parse_trial_balance_csv(csv_text)
        self.assertEqual(tb.ledgers[0].name, "Cash")
        self.assertEqual(tb.ledgers[0].group, "Current Assets")

    def test_missing_debit_column_raises_with_clear_message(self):
        csv_text = "Ledger Name,Group,Credit\nCash,Assets,0.00\n"
        with self.assertRaises(TrialBalanceParseError) as ctx:
            parse_trial_balance_csv(csv_text)
        self.assertIn("Debit", str(ctx.exception))
        self.assertIn("Actual columns found", str(ctx.exception))


class NumberCleanupTests(unittest.TestCase):
    def test_currency_symbols_and_thousands_separators(self):
        csv_text = (
            "Ledger Name,Group,Debit,Credit\n"
            "Cash,Assets,\"Rs. 12,50,000.00\",INR 0.00\n"
        )
        tb = parse_trial_balance_csv(csv_text)
        self.assertEqual(tb.ledgers[0].debit, Decimal("1250000.00"))
        self.assertEqual(tb.ledgers[0].credit, Decimal("0.00"))

    def test_parenthesized_negative(self):
        csv_text = "Ledger Name,Group,Debit,Credit\nX,Assets,(500.00),0.00\n"
        tb = parse_trial_balance_csv(csv_text)
        self.assertEqual(tb.ledgers[0].debit, Decimal("-500.00"))

    def test_blank_cell_treated_as_zero(self):
        csv_text = "Ledger Name,Group,Debit,Credit\nX,Assets,500.00,\n"
        tb = parse_trial_balance_csv(csv_text)
        self.assertEqual(tb.ledgers[0].credit, Decimal("0.00"))

    def test_unparseable_number_raises_with_row_and_ledger(self):
        csv_text = "Ledger Name,Group,Debit,Credit\nCash,Assets,not-a-number,0.00\n"
        with self.assertRaises(TrialBalanceParseError) as ctx:
            parse_trial_balance_csv(csv_text)
        self.assertIn("Row 2", str(ctx.exception))
        self.assertIn("Cash", str(ctx.exception))


class RejectionTests(unittest.TestCase):
    def test_empty_file_raises(self):
        with self.assertRaises(TrialBalanceParseError):
            parse_trial_balance_csv("")

    def test_header_only_no_rows_raises(self):
        with self.assertRaises(TrialBalanceParseError):
            parse_trial_balance_csv("Ledger Name,Group,Debit,Credit\n")

    def test_empty_ledger_name_raises(self):
        csv_text = "Ledger Name,Group,Debit,Credit\n,Assets,500.00,0.00\n"
        with self.assertRaises(TrialBalanceParseError):
            parse_trial_balance_csv(csv_text)

    def test_duplicate_ledger_name_raises(self):
        csv_text = (
            "Ledger Name,Group,Debit,Credit\n"
            "Cash,Assets,500.00,0.00\n"
            "Cash,Assets,600.00,0.00\n"
        )
        with self.assertRaises(TrialBalanceParseError) as ctx:
            parse_trial_balance_csv(csv_text)
        self.assertIn("duplicate", str(ctx.exception).lower())

    def test_unrelated_csv_raises(self):
        csv_text = "Product,Price,Quantity\nWidget,9.99,3\n"
        with self.assertRaises(TrialBalanceParseError):
            parse_trial_balance_csv(csv_text)


if __name__ == "__main__":
    unittest.main()
