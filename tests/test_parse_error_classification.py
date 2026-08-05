import unittest

from parse_error_classification import classify_parse_error
from tally_xml_parser import (
    TallyXmlEncodingError,
    TallyXmlMalformedError,
    TallyXmlNotATallyExportError,
    TallyXmlParseError,
    TallyXmlTruncatedError,
)
from trial_balance_csv_parser import TrialBalanceParseError


class ClassificationTests(unittest.TestCase):
    def test_truncated_error_classified_as_file_truncated(self):
        result = classify_parse_error(TallyXmlTruncatedError("no element found: line 563384, column 7"))
        self.assertEqual(result.status_code, 422)
        self.assertEqual(result.category, "file_truncated")
        self.assertTrue(result.is_file_problem)
        self.assertIn("incomplete", result.message)
        self.assertIn("re-export", result.suggested_fix.lower())
        self.assertIn("no element found", result.technical_detail)

    def test_encoding_error_classified_as_unsupported_encoding(self):
        result = classify_parse_error(TallyXmlEncodingError("Could not decode file as UTF-8 or UTF-16"))
        self.assertEqual(result.status_code, 422)
        self.assertEqual(result.category, "unsupported_encoding")
        self.assertTrue(result.is_file_problem)

    def test_python_unicode_decode_error_also_classified_as_unsupported_encoding(self):
        # The CSV endpoint raises a plain UnicodeDecodeError directly (not
        # wrapped in a TallyXmlEncodingError) -- must be recognized too.
        try:
            b"\x80\x81".decode("utf-8")
        except UnicodeDecodeError as e:
            result = classify_parse_error(e)
        self.assertEqual(result.status_code, 422)
        self.assertEqual(result.category, "unsupported_encoding")
        self.assertTrue(result.is_file_problem)

    def test_not_a_tally_export_error_classified_correctly(self):
        result = classify_parse_error(TallyXmlNotATallyExportError("No <LEDGER> master elements found"))
        self.assertEqual(result.status_code, 422)
        self.assertEqual(result.category, "not_a_tally_export")
        self.assertTrue(result.is_file_problem)

    def test_malformed_xml_error_classified_as_not_valid_xml(self):
        result = classify_parse_error(TallyXmlMalformedError("Not well-formed XML: undefined entity"))
        self.assertEqual(result.status_code, 422)
        self.assertEqual(result.category, "not_valid_xml")
        self.assertTrue(result.is_file_problem)

    def test_base_tally_xml_parse_error_classified_as_file_data_issue(self):
        # Raised directly (not one of the named subclasses) -- e.g. a
        # duplicate ledger or a voucher whose legs don't sum to zero.
        message = "Voucher 'SI-0001': ledger entries do not sum to zero (got 500.00) -- not a valid double-entry voucher."
        result = classify_parse_error(TallyXmlParseError(message))
        self.assertEqual(result.status_code, 422)
        self.assertEqual(result.category, "file_data_issue")
        self.assertTrue(result.is_file_problem)
        # The existing message is already reasonably clear -- reused as-is
        # rather than replaced with something more generic.
        self.assertEqual(result.message, message)

    def test_trial_balance_parse_error_also_classified_as_file_data_issue(self):
        result = classify_parse_error(TrialBalanceParseError("Missing required column: Debit"))
        self.assertEqual(result.status_code, 422)
        self.assertEqual(result.category, "file_data_issue")
        self.assertTrue(result.is_file_problem)

    def test_unrecognized_exception_classified_as_unknown_and_flagged_as_product_issue(self):
        # A genuinely unexpected exception -- not any of this project's own
        # parse-error types at all -- must be flagged as OUR bug, not the
        # user's file, and returned as a 500, not a 422.
        result = classify_parse_error(KeyError("some_unexpected_internal_bug"))
        self.assertEqual(result.status_code, 500)
        self.assertEqual(result.category, "unknown")
        self.assertFalse(result.is_file_problem)
        self.assertIn("our end", result.message)
        self.assertIn("KeyError", result.technical_detail)

    def test_subclasses_are_not_misclassified_as_the_generic_file_data_issue_bucket(self):
        # Every named subclass must be caught by its own specific branch,
        # not fall through to the generic TallyXmlParseError bucket -- this
        # would still "work" (subclasses ARE TallyXmlParseError) but would
        # silently lose the more specific, more helpful category.
        for error_cls, expected_category in [
            (TallyXmlTruncatedError, "file_truncated"),
            (TallyXmlEncodingError, "unsupported_encoding"),
            (TallyXmlNotATallyExportError, "not_a_tally_export"),
            (TallyXmlMalformedError, "not_valid_xml"),
        ]:
            with self.subTest(error_cls=error_cls):
                result = classify_parse_error(error_cls("some message"))
                self.assertEqual(result.category, expected_category)

    def test_to_dict_shape_matches_frontend_contract(self):
        result = classify_parse_error(TallyXmlTruncatedError("no element found"))
        body = result.to_dict()
        self.assertEqual(
            set(body.keys()),
            {"category", "is_file_problem", "message", "suggested_fix", "technical_detail"},
        )
        # status_code is NOT part of the JSON body -- it's the HTTP status
        # itself, set separately by the caller (api.py).
        self.assertNotIn("status_code", body)


if __name__ == "__main__":
    unittest.main()
