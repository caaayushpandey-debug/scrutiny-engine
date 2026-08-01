"""Tests for coordinator.py -- does it correctly report which checks can/
cannot run given what's been uploaded for a client/FY/version, per the
CLAUDE.md HARD RULE #7 scope resolution rules.
"""
import unittest

from checks.opening_balance_vs_prior_year_closing import CHECK_ID as OPENING_BALANCE_CHECK_ID
from checks.registry import CHECK_REGISTRY
from coordinator import AvailableDocuments, evaluate_all_checks, evaluate_check_readiness
from schemas.enums import DocumentScope, DocumentType


def _opening_balance_check_def():
    [check_def] = [c for c in CHECK_REGISTRY if c.check_id == OPENING_BALANCE_CHECK_ID]
    return check_def


class RegistryTests(unittest.TestCase):
    def test_check_1_requirements_have_expected_scopes(self):
        check_def = _opening_balance_check_def()
        scopes_by_role = {r.role: r.scope for r in check_def.requirements}

        self.assertEqual(
            scopes_by_role["prior_year_trial_balance"],
            DocumentScope.PERIOD_SCOPED_PRIOR_YEAR,
        )
        self.assertEqual(
            scopes_by_role["current_year_trial_balance"],
            DocumentScope.VERSION_SCOPED,
        )
        self.assertTrue(all(r.document_type == DocumentType.TRIAL_BALANCE for r in check_def.requirements))


class CoordinatorReadinessTests(unittest.TestCase):
    def test_can_run_when_both_documents_present(self):
        available = AvailableDocuments(client_id="client-1", fy="2025-26")
        available.add_period_scoped(DocumentType.TRIAL_BALANCE, DocumentScope.PERIOD_SCOPED_PRIOR_YEAR)
        available.add_version_scoped("v2", DocumentType.TRIAL_BALANCE)

        readiness = evaluate_check_readiness(_opening_balance_check_def(), available, version_id="v2")

        self.assertTrue(readiness.can_run)
        self.assertEqual(readiness.missing, [])

    def test_cannot_run_missing_prior_year(self):
        available = AvailableDocuments(client_id="client-1", fy="2025-26")
        available.add_version_scoped("v2", DocumentType.TRIAL_BALANCE)
        # prior-year trial balance deliberately withheld

        readiness = evaluate_check_readiness(_opening_balance_check_def(), available, version_id="v2")

        self.assertFalse(readiness.can_run)
        self.assertEqual(len(readiness.missing), 1)
        self.assertEqual(readiness.missing[0].role, "prior_year_trial_balance")
        self.assertIn("Prior year closing trial balance", readiness.missing_descriptions())

    def test_cannot_run_missing_current_year(self):
        available = AvailableDocuments(client_id="client-1", fy="2025-26")
        available.add_period_scoped(DocumentType.TRIAL_BALANCE, DocumentScope.PERIOD_SCOPED_PRIOR_YEAR)
        # current-year (version-scoped) trial balance deliberately withheld

        readiness = evaluate_check_readiness(_opening_balance_check_def(), available, version_id="v2")

        self.assertFalse(readiness.can_run)
        self.assertEqual(len(readiness.missing), 1)
        self.assertEqual(readiness.missing[0].role, "current_year_trial_balance")

    def test_cannot_run_missing_both(self):
        available = AvailableDocuments(client_id="client-1", fy="2025-26")

        readiness = evaluate_check_readiness(_opening_balance_check_def(), available, version_id="v2")

        self.assertFalse(readiness.can_run)
        self.assertEqual(len(readiness.missing), 2)

    def test_version_scoped_document_only_satisfies_its_own_version(self):
        # Uploaded for v1, but we're scrutinizing v2 -- must not count.
        available = AvailableDocuments(client_id="client-1", fy="2025-26")
        available.add_period_scoped(DocumentType.TRIAL_BALANCE, DocumentScope.PERIOD_SCOPED_PRIOR_YEAR)
        available.add_version_scoped("v1", DocumentType.TRIAL_BALANCE)

        readiness = evaluate_check_readiness(_opening_balance_check_def(), available, version_id="v2")

        self.assertFalse(readiness.can_run)
        self.assertEqual(readiness.missing[0].role, "current_year_trial_balance")

    def test_period_scoped_document_satisfies_regardless_of_which_version_is_scrutinized(self):
        available = AvailableDocuments(client_id="client-1", fy="2025-26")
        available.add_period_scoped(DocumentType.TRIAL_BALANCE, DocumentScope.PERIOD_SCOPED_PRIOR_YEAR)
        available.add_version_scoped("v1", DocumentType.TRIAL_BALANCE)
        available.add_version_scoped("v2", DocumentType.TRIAL_BALANCE)
        available.add_version_scoped("v3", DocumentType.TRIAL_BALANCE)

        for version_id in ("v1", "v2", "v3"):
            readiness = evaluate_check_readiness(_opening_balance_check_def(), available, version_id=version_id)
            self.assertTrue(readiness.can_run, f"expected can_run for {version_id}")

    def test_evaluate_all_checks_reports_every_registered_check(self):
        available = AvailableDocuments(client_id="client-1", fy="2025-26")

        results = evaluate_all_checks(available, version_id="v1")

        self.assertEqual({r.check_id for r in results}, {c.check_id for c in CHECK_REGISTRY})


if __name__ == "__main__":
    unittest.main()
