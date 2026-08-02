"""Manual, standalone smoke test for api.py against a running server on
localhost:8000 -- converts a real data-synthesizer sample (CSV) to the API's
JSON shape, posts it, and checks the flagged ledgers match that sample's
answer key exactly. Not part of `python3 -m unittest discover` (needs a live
server + the sibling data-synthesizer repo, same reasoning as
tests/verify_against_data_synthesizer.py). Run manually:

    python3 tests/test_api_manual.py
"""
import csv
import json
import sys
import urllib.request
from pathlib import Path

SAMPLE_DIR = Path(__file__).parent.parent.parent / "data-synthesizer" / "samples" / "trial_balance" / "05_coral_bay_infratech_pvt_ltd"
API_URL = "http://localhost:8000/run-checks"


def load_ledgers(csv_path: Path) -> list:
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        return [
            {"name": row["Ledger Name"].strip(), "group": row["Group"].strip(),
             "debit": row["Debit"].strip(), "credit": row["Credit"].strip()}
            for row in reader
        ]


def main():
    prior = load_ledgers(SAMPLE_DIR / "prior_year_closing_trial_balance.csv")
    current = load_ledgers(SAMPLE_DIR / "current_year_opening_trial_balance.csv")
    answer_key = json.loads((SAMPLE_DIR / "answer_key.json").read_text())
    expected_flagged_names = {e["ledger_name"] for e in answer_key["injected_errors"]}

    payload = {
        "prior_year_trial_balance": {"ledgers": prior},
        "current_year_trial_balance": {"ledgers": current},
    }
    req = urllib.request.Request(
        API_URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        results = json.loads(resp.read())

    flagged_names = {r["source_reference"]["ledger"] for r in results if r["status"] == "flagged"}

    print(f"Ledgers checked: {len(results)}")
    print(f"Expected flagged ({len(expected_flagged_names)}): {sorted(expected_flagged_names)}")
    print(f"Actually flagged ({len(flagged_names)}): {sorted(flagged_names)}")

    missed = expected_flagged_names - flagged_names
    extra = flagged_names - expected_flagged_names
    if missed:
        print(f"FAIL: missed {len(missed)} expected errors: {missed}")
    if extra:
        print(f"FAIL: {len(extra)} false positives: {extra}")
    if not missed and not extra:
        print("PASS: flagged ledgers exactly match the answer key.")
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
