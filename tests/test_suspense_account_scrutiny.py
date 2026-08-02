import unittest
from decimal import Decimal

from checks.suspense_account_scrutiny import run_check, run_check_from_file
from schemas.tally_data import TallyData, TallyLedgerMaster, TallyVoucher, TallyVoucherLeg


def ledger(name, parent="Sundry Debtors", opening="0.00"):
    return TallyLedgerMaster(name=name, parent=parent, opening_balance=Decimal(opening))


def voucher(vn, date, legs, vch_type="Journal", narration="Test narration."):
    return TallyVoucher(vch_type=vch_type, voucher_number=vn, date=date, narration=narration, legs=legs)


def leg(name, is_debit, amount):
    return TallyVoucherLeg(ledger_name=name, is_debit=is_debit, amount=Decimal(amount))


class NoSuspenseActivityTests(unittest.TestCase):
    def test_no_suspense_ledger_at_all_passes(self):
        data = TallyData(
            ledgers={"HDFC Bank": ledger("HDFC Bank", "Bank Accounts", "10000.00")},
            vouchers=[voucher("JV-0001", "2025-04-01", [leg("HDFC Bank", True, "-500.00"), leg("Office Expenses", False, "500.00")])],
        )
        results = run_check(data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "pass")

    def test_suspense_ledger_present_but_untouched_passes(self):
        data = TallyData(
            ledgers={
                "HDFC Bank": ledger("HDFC Bank", "Bank Accounts", "10000.00"),
                "Suspense Account": ledger("Suspense Account", "Indirect Expenses", "0.00"),
            },
            vouchers=[voucher("JV-0001", "2025-04-01", [leg("HDFC Bank", True, "-500.00"), leg("Office Expenses", False, "500.00")])],
        )
        results = run_check(data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "pass")


class FlaggingTests(unittest.TestCase):
    def test_voucher_touching_suspense_is_flagged(self):
        data = TallyData(
            ledgers={
                "Axis Bank CC A/c": ledger("Axis Bank CC A/c", "Bank Accounts", "100000.00"),
                "Suspense Account": ledger("Suspense Account", "Indirect Expenses", "0.00"),
            },
            vouchers=[voucher(
                "JV-0012", "2025-11-20",
                [leg("Axis Bank CC A/c", False, "12000.00"), leg("Suspense Account", True, "-12000.00")],
                narration="Being reconciliation difference adjusted.",
            )],
        )
        results = run_check(data)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r.status, "flagged")
        self.assertEqual(r.source_reference.ledger, "Axis Bank CC A/c")
        self.assertEqual(r.source_reference.voucher_number, "JV-0012")
        self.assertEqual(r.source_reference.date, "2025-11-20")
        self.assertEqual(r.amount, Decimal("12000.00"))
        self.assertIn("reconciliation difference", r.finding)

    def test_case_insensitive_suspense_name_match(self):
        data = TallyData(
            ledgers={
                "HDFC Bank": ledger("HDFC Bank", "Bank Accounts", "5000.00"),
                "Suspense A/c": ledger("Suspense A/c", "Indirect Expenses", "0.00"),
            },
            vouchers=[voucher("JV-0002", "2025-06-01", [leg("HDFC Bank", True, "-750.00"), leg("Suspense A/c", False, "750.00")])],
        )
        results = run_check(data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "flagged")

    def test_multiple_flagged_results_sorted_by_date(self):
        data = TallyData(
            ledgers={
                "HDFC Bank": ledger("HDFC Bank", "Bank Accounts", "5000.00"),
                "Sundry Creditors - ABC": ledger("Sundry Creditors - ABC", "Sundry Creditors", "-2000.00"),
                "Suspense Account": ledger("Suspense Account", "Indirect Expenses", "0.00"),
            },
            vouchers=[
                voucher("JV-0002", "2025-11-01", [leg("Sundry Creditors - ABC", False, "300.00"), leg("Suspense Account", True, "-300.00")]),
                voucher("JV-0001", "2025-05-01", [leg("HDFC Bank", True, "-750.00"), leg("Suspense Account", False, "750.00")]),
            ],
        )
        results = run_check(data)
        self.assertEqual(len(results), 2)
        self.assertEqual([r.source_reference.voucher_number for r in results], ["JV-0001", "JV-0002"])

    def test_flagged_results_carry_all_hard_rule_6_fields(self):
        data = TallyData(
            ledgers={
                "HDFC Bank": ledger("HDFC Bank", "Bank Accounts", "5000.00"),
                "Suspense Account": ledger("Suspense Account", "Indirect Expenses", "0.00"),
            },
            vouchers=[voucher("JV-0001", "2025-05-01", [leg("HDFC Bank", True, "-750.00"), leg("Suspense Account", False, "750.00")])],
        )
        r = run_check(data)[0]
        for field_name in ("finding", "potential_implication", "recommended_manual_check", "why_correction_matters"):
            value = getattr(r, field_name)
            self.assertTrue(value and value.strip(), f"{field_name} should be non-empty")


class RunCheckFromFileTests(unittest.TestCase):
    def test_missing_file_returns_insufficient_data(self):
        results = run_check_from_file("/nonexistent/path/tally_export.xml")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "insufficient_data")


if __name__ == "__main__":
    unittest.main()
