"""Basic sanity tests for checks/opening_balance_vs_prior_year_closing.py,
using small hand-built fixtures.

NOTE: these tests alone only prove the matching/tolerance/missing-ledger
logic behaves as intended on a few constructed examples -- they are not what
makes this check "final" under CLAUDE.md HARD RULE #4. That validation is in
tests/verify_against_data_synthesizer.py, which runs the check against real
data-synthesizer output and diffs the result against each company's
answer_key.json programmatically. It has been run and passed; see that
script's docstring and the check module's own docstring for the result.
"""
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from checks.opening_balance_vs_prior_year_closing import (
    CHECK_ID,
    LedgerBalance,
    load_trial_balance_csv,
    run_check,
)


def lb(name, group, debit="0.00", credit="0.00"):
    return LedgerBalance(name=name, group=group, debit=Decimal(debit), credit=Decimal(credit))


class RunCheckTests(unittest.TestCase):
    def test_matching_balance_passes(self):
        prior = [lb("Cash in Hand", "Cash-in-Hand", debit="63168.89")]
        current = [lb("Cash in Hand", "Cash-in-Hand", debit="63168.89")]

        [result] = run_check(prior, current)

        self.assertEqual(result.check_id, CHECK_ID)
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.confidence_score, 1.0)
        self.assertEqual(result.amount, Decimal("63168.89"))
        self.assertEqual(result.source_reference.ledger, "Cash in Hand")

    def test_diff_within_tolerance_passes(self):
        prior = [lb("Cash in Hand", "Cash-in-Hand", debit="1000.00")]
        current = [lb("Cash in Hand", "Cash-in-Hand", debit="1000.50")]

        [result] = run_check(prior, current, tolerance=Decimal("1.00"))

        self.assertEqual(result.status, "pass")

    def test_diff_exactly_at_tolerance_boundary_passes(self):
        prior = [lb("Cash in Hand", "Cash-in-Hand", debit="1000.00")]
        current = [lb("Cash in Hand", "Cash-in-Hand", debit="1001.00")]

        [result] = run_check(prior, current, tolerance=Decimal("1.00"))

        self.assertEqual(result.status, "pass")

    def test_diff_beyond_tolerance_flagged(self):
        # Mirrors data-synthesizer's actual injected-error shape: Office
        # Building 2096831.52 -> 2004157.38 (delta -92674.14).
        prior = [lb("Office Building", "Fixed Assets", debit="2096831.52")]
        current = [lb("Office Building", "Fixed Assets", debit="2004157.38")]

        [result] = run_check(prior, current)

        self.assertEqual(result.status, "flagged")
        self.assertEqual(result.confidence_score, 1.0)
        self.assertEqual(result.amount, Decimal("92674.14"))
        self.assertIn("Office Building", result.description)

    def test_credit_side_mismatch_flagged(self):
        # Partner's Capital Account is credit-normal; net balance = credit - debit...
        # net_balance is debit - credit, so a credit-only ledger has a negative net balance.
        prior = [lb("Partner's Capital Account", "Capital Account", credit="4468672.27")]
        current = [lb("Partner's Capital Account", "Capital Account", credit="4542230.60")]

        [result] = run_check(prior, current)

        self.assertEqual(result.status, "flagged")
        self.assertEqual(result.amount, Decimal("73558.33"))

    def test_ledger_missing_from_current_year_is_insufficient_data(self):
        prior = [lb("Sundry Debtors - Acme Pvt Ltd", "Sundry Debtors", debit="500000.00")]
        current = []

        [result] = run_check(prior, current)

        self.assertEqual(result.status, "insufficient_data")
        self.assertEqual(result.amount, Decimal("500000.00"))
        self.assertIn("missing entirely", result.description)

    def test_ledger_missing_from_prior_year_is_insufficient_data(self):
        prior = []
        current = [lb("Sundry Debtors - New Client Pvt Ltd", "Sundry Debtors", debit="200000.00")]

        [result] = run_check(prior, current)

        self.assertEqual(result.status, "insufficient_data")
        self.assertEqual(result.amount, Decimal("200000.00"))
        self.assertIn("not present in the prior year", result.description)

    def test_mixed_batch_produces_expected_status_counts(self):
        prior = [
            lb("Cash in Hand", "Cash-in-Hand", debit="63168.89"),
            lb("Office Building", "Fixed Assets", debit="2096831.52"),
            lb("Sundry Debtors - Only In Prior", "Sundry Debtors", debit="10000.00"),
        ]
        current = [
            lb("Cash in Hand", "Cash-in-Hand", debit="63168.89"),
            lb("Office Building", "Fixed Assets", debit="2004157.38"),
            lb("Sundry Debtors - Only In Current", "Sundry Debtors", debit="20000.00"),
        ]

        results = run_check(prior, current)
        statuses = [r.status for r in results]

        self.assertEqual(statuses.count("pass"), 1)
        self.assertEqual(statuses.count("flagged"), 1)
        self.assertEqual(statuses.count("insufficient_data"), 2)
        self.assertEqual(len(results), 4)

    def test_results_sorted_by_ledger_name(self):
        prior = [
            lb("Zebra Ledger", "Sundry Creditors", credit="1000.00"),
            lb("Alpha Ledger", "Sundry Debtors", debit="1000.00"),
        ]
        current = [
            lb("Zebra Ledger", "Sundry Creditors", credit="1000.00"),
            lb("Alpha Ledger", "Sundry Debtors", debit="1000.00"),
        ]

        results = run_check(prior, current)

        self.assertEqual([r.source_reference.ledger for r in results], ["Alpha Ledger", "Zebra Ledger"])


class LoadTrialBalanceCsvTests(unittest.TestCase):
    def _write_csv(self, tmpdir, content):
        path = Path(tmpdir) / "tb.csv"
        path.write_text(content)
        return str(path)

    def test_loads_valid_csv(self):
        content = (
            "Ledger Name,Group,Debit,Credit\n"
            "Cash in Hand,Cash-in-Hand,63168.89,0.00\n"
            "Partner's Capital Account,Capital Account,0.00,11640156.50\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_csv(tmpdir, content)
            rows = load_trial_balance_csv(path)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].name, "Cash in Hand")
        self.assertEqual(rows[0].debit, Decimal("63168.89"))
        self.assertEqual(rows[1].net_balance, Decimal("-11640156.50"))

    def test_duplicate_ledger_name_raises(self):
        content = (
            "Ledger Name,Group,Debit,Credit\n"
            "Cash in Hand,Cash-in-Hand,1000.00,0.00\n"
            "Cash in Hand,Cash-in-Hand,2000.00,0.00\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_csv(tmpdir, content)
            with self.assertRaises(ValueError):
                load_trial_balance_csv(path)

    def test_malformed_columns_raises(self):
        content = "Name,Debit,Credit\nCash,100,0\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_csv(tmpdir, content)
            with self.assertRaises(ValueError):
                load_trial_balance_csv(path)

    def test_non_numeric_amount_raises(self):
        content = "Ledger Name,Group,Debit,Credit\nCash in Hand,Cash-in-Hand,abc,0.00\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_csv(tmpdir, content)
            with self.assertRaises(ValueError):
                load_trial_balance_csv(path)


if __name__ == "__main__":
    unittest.main()
