"""Tests for schemas/trial_balance.py -- moved here from
test_opening_balance_vs_prior_year_closing.py since CSV loading is now a
schema-layer concern, not specific to any one check.
"""
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from schemas.trial_balance import TrialBalance


class TrialBalanceFromCsvTests(unittest.TestCase):
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
            trial_balance = TrialBalance.from_csv(path)

        self.assertEqual(len(trial_balance.ledgers), 2)
        self.assertEqual(trial_balance.ledgers[0].name, "Cash in Hand")
        self.assertEqual(trial_balance.ledgers[0].debit, Decimal("63168.89"))
        self.assertEqual(trial_balance.ledgers[1].net_balance, Decimal("-11640156.50"))

    def test_duplicate_ledger_name_raises(self):
        content = (
            "Ledger Name,Group,Debit,Credit\n"
            "Cash in Hand,Cash-in-Hand,1000.00,0.00\n"
            "Cash in Hand,Cash-in-Hand,2000.00,0.00\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_csv(tmpdir, content)
            with self.assertRaises(ValueError):
                TrialBalance.from_csv(path)

    def test_malformed_columns_raises(self):
        content = "Name,Debit,Credit\nCash,100,0\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_csv(tmpdir, content)
            with self.assertRaises(ValueError):
                TrialBalance.from_csv(path)

    def test_non_numeric_amount_raises(self):
        content = "Ledger Name,Group,Debit,Credit\nCash in Hand,Cash-in-Hand,abc,0.00\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_csv(tmpdir, content)
            with self.assertRaises(ValueError):
                TrialBalance.from_csv(path)

    def test_missing_file_raises_oserror(self):
        with self.assertRaises(OSError):
            TrialBalance.from_csv("/nonexistent/path/does_not_exist.csv")


if __name__ == "__main__":
    unittest.main()
