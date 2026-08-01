"""Basic sanity tests for checks/opening_balance_vs_prior_year_closing.py,
using small hand-built fixtures.

NOTE: these tests alone only prove the matching/tolerance/missing-ledger
logic behaves as intended on a few constructed examples -- they are not what
makes this check "final" under CLAUDE.md HARD RULE #4. That validation is in
tests/verify_against_data_synthesizer.py, which runs the check against real
data-synthesizer output and diffs the result against each company's
answer_key.json programmatically. It has been run and passed; see that
script's docstring and the check module's own docstring for the result.

CSV-loading tests live in tests/test_schemas_trial_balance.py -- that's now
a schema-layer concern (schemas/trial_balance.py), not a check-layer one.
"""
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from checks.opening_balance_vs_prior_year_closing import (
    CHECK_ID,
    run_check,
    run_check_from_files,
)
from schemas.trial_balance import LedgerBalance, TrialBalance

FLAGGED_DETAIL_FIELDS = ("finding", "potential_implication", "recommended_manual_check", "why_correction_matters")


def lb(name, group, debit="0.00", credit="0.00"):
    return LedgerBalance(name=name, group=group, debit=Decimal(debit), credit=Decimal(credit))


def tb(*ledgers):
    return TrialBalance(ledgers=list(ledgers))


def assert_has_flagged_detail(test_case, result):
    """HARD RULE #6: every flagged result must carry all four structured
    explanation fields, each non-empty.
    """
    test_case.assertEqual(result.status, "flagged")
    for field_name in FLAGGED_DETAIL_FIELDS:
        value = getattr(result, field_name)
        test_case.assertIsInstance(value, str, f"{field_name} should be a populated string")
        test_case.assertTrue(value.strip(), f"{field_name} should not be empty")


def assert_no_flagged_detail(test_case, result):
    """pass/insufficient_data results must NOT carry the flagged-only fields."""
    for field_name in FLAGGED_DETAIL_FIELDS:
        test_case.assertIsNone(getattr(result, field_name), f"{field_name} should be None for status={result.status}")


class RunCheckTests(unittest.TestCase):
    def test_matching_balance_passes(self):
        prior = tb(lb("Cash in Hand", "Cash-in-Hand", debit="63168.89"))
        current = tb(lb("Cash in Hand", "Cash-in-Hand", debit="63168.89"))

        [result] = run_check(prior, current)

        self.assertEqual(result.check_id, CHECK_ID)
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.confidence_score, 1.0)
        self.assertEqual(result.amount, Decimal("63168.89"))
        self.assertEqual(result.source_reference.ledger, "Cash in Hand")
        assert_no_flagged_detail(self, result)

    def test_diff_within_tolerance_passes(self):
        prior = tb(lb("Cash in Hand", "Cash-in-Hand", debit="1000.00"))
        current = tb(lb("Cash in Hand", "Cash-in-Hand", debit="1000.50"))

        [result] = run_check(prior, current, tolerance=Decimal("1.00"))

        self.assertEqual(result.status, "pass")

    def test_diff_exactly_at_tolerance_boundary_passes(self):
        prior = tb(lb("Cash in Hand", "Cash-in-Hand", debit="1000.00"))
        current = tb(lb("Cash in Hand", "Cash-in-Hand", debit="1001.00"))

        [result] = run_check(prior, current, tolerance=Decimal("1.00"))

        self.assertEqual(result.status, "pass")

    def test_diff_beyond_tolerance_flagged_with_full_detail(self):
        # Mirrors data-synthesizer's actual injected-error shape: Office
        # Building 2096831.52 -> 2004157.38 (delta -92674.14).
        prior = tb(lb("Office Building", "Fixed Assets", debit="2096831.52"))
        current = tb(lb("Office Building", "Fixed Assets", debit="2004157.38"))

        [result] = run_check(prior, current)

        self.assertEqual(result.status, "flagged")
        self.assertEqual(result.confidence_score, 1.0)
        self.assertEqual(result.amount, Decimal("92674.14"))
        self.assertIn("Office Building", result.finding)
        self.assertIn("Office Building", result.description)  # description mirrors finding
        assert_has_flagged_detail(self, result)

    def test_credit_side_mismatch_flagged(self):
        # Partner's Capital Account is credit-normal; net_balance = debit - credit
        # is negative for a pure-credit ledger, which is fine -- the diff is
        # what's compared, not the sign.
        prior = tb(lb("Partner's Capital Account", "Capital Account", credit="4468672.27"))
        current = tb(lb("Partner's Capital Account", "Capital Account", credit="4542230.60"))

        [result] = run_check(prior, current)

        self.assertEqual(result.status, "flagged")
        self.assertEqual(result.amount, Decimal("73558.33"))
        assert_has_flagged_detail(self, result)

    def test_ledger_missing_from_current_year_is_flagged(self):
        prior = tb(lb("Sundry Debtors - Acme Pvt Ltd", "Sundry Debtors", debit="500000.00"))
        current = tb()

        [result] = run_check(prior, current)

        self.assertEqual(result.status, "flagged")
        self.assertEqual(result.amount, Decimal("500000.00"))
        self.assertIn("does not appear anywhere", result.finding)
        assert_has_flagged_detail(self, result)

    def test_ledger_missing_from_prior_year_is_flagged(self):
        prior = tb()
        current = tb(lb("Sundry Debtors - New Client Pvt Ltd", "Sundry Debtors", debit="200000.00"))

        [result] = run_check(prior, current)

        self.assertEqual(result.status, "flagged")
        self.assertEqual(result.amount, Decimal("200000.00"))
        self.assertIn("was not present in the prior year", result.finding)
        assert_has_flagged_detail(self, result)

    def test_run_check_never_returns_insufficient_data(self):
        # By design (see module docstring "status semantics") -- missing
        # ledgers are "flagged", not "insufficient_data". insufficient_data
        # only comes from run_check_from_files failing to load a file.
        prior = tb(lb("Only In Prior", "Sundry Debtors", debit="1.00"))
        current = tb(lb("Only In Current", "Sundry Debtors", debit="1.00"))

        results = run_check(prior, current)

        self.assertTrue(all(r.status != "insufficient_data" for r in results))

    def test_mixed_batch_produces_expected_status_counts(self):
        prior = tb(
            lb("Cash in Hand", "Cash-in-Hand", debit="63168.89"),
            lb("Office Building", "Fixed Assets", debit="2096831.52"),
            lb("Sundry Debtors - Only In Prior", "Sundry Debtors", debit="10000.00"),
        )
        current = tb(
            lb("Cash in Hand", "Cash-in-Hand", debit="63168.89"),
            lb("Office Building", "Fixed Assets", debit="2004157.38"),
            lb("Sundry Debtors - Only In Current", "Sundry Debtors", debit="20000.00"),
        )

        results = run_check(prior, current)
        statuses = [r.status for r in results]

        self.assertEqual(statuses.count("pass"), 1)
        self.assertEqual(statuses.count("flagged"), 3)  # amount mismatch + 2 missing-ledger cases
        self.assertEqual(statuses.count("insufficient_data"), 0)
        self.assertEqual(len(results), 4)
        for r in results:
            if r.status == "flagged":
                assert_has_flagged_detail(self, r)

    def test_results_sorted_by_ledger_name(self):
        prior = tb(
            lb("Zebra Ledger", "Sundry Creditors", credit="1000.00"),
            lb("Alpha Ledger", "Sundry Debtors", debit="1000.00"),
        )
        current = tb(
            lb("Zebra Ledger", "Sundry Creditors", credit="1000.00"),
            lb("Alpha Ledger", "Sundry Debtors", debit="1000.00"),
        )

        results = run_check(prior, current)

        self.assertEqual([r.source_reference.ledger for r in results], ["Alpha Ledger", "Zebra Ledger"])


class RunCheckFromFilesTests(unittest.TestCase):
    def _write_csv(self, tmpdir, filename, content):
        path = Path(tmpdir) / filename
        path.write_text(content)
        return str(path)

    def test_missing_prior_year_file_is_insufficient_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            current_path = self._write_csv(
                tmpdir, "current.csv",
                "Ledger Name,Group,Debit,Credit\nCash in Hand,Cash-in-Hand,100.00,0.00\n",
            )
            results = run_check_from_files(str(Path(tmpdir) / "does_not_exist.csv"), current_path)

        [result] = results
        self.assertEqual(result.status, "insufficient_data")
        self.assertIn("prior year closing", result.description)
        assert_no_flagged_detail(self, result)

    def test_missing_current_year_file_is_insufficient_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            prior_path = self._write_csv(
                tmpdir, "prior.csv",
                "Ledger Name,Group,Debit,Credit\nCash in Hand,Cash-in-Hand,100.00,0.00\n",
            )
            results = run_check_from_files(prior_path, str(Path(tmpdir) / "does_not_exist.csv"))

        [result] = results
        self.assertEqual(result.status, "insufficient_data")
        self.assertIn("current year opening", result.description)

    def test_corrupted_prior_year_file_is_insufficient_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            prior_path = self._write_csv(tmpdir, "prior.csv", "not,a,valid,trial,balance,csv\n1,2,3,4,5,6\n")
            current_path = self._write_csv(
                tmpdir, "current.csv",
                "Ledger Name,Group,Debit,Credit\nCash in Hand,Cash-in-Hand,100.00,0.00\n",
            )
            results = run_check_from_files(prior_path, current_path)

        [result] = results
        self.assertEqual(result.status, "insufficient_data")

    def test_well_formed_files_run_normally(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            prior_path = self._write_csv(
                tmpdir, "prior.csv",
                "Ledger Name,Group,Debit,Credit\nCash in Hand,Cash-in-Hand,100.00,0.00\n",
            )
            current_path = self._write_csv(
                tmpdir, "current.csv",
                "Ledger Name,Group,Debit,Credit\nCash in Hand,Cash-in-Hand,100.00,0.00\n",
            )
            results = run_check_from_files(prior_path, current_path)

        [result] = results
        self.assertEqual(result.status, "pass")


if __name__ == "__main__":
    unittest.main()
