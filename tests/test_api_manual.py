"""Manual, standalone full-stack smoke test for api.py against a running
server on localhost:8000 -- uploads a real data-synthesizer sample's raw CSV
files to /parse-trial-balance, feeds the parsed ledgers straight into
/run-checks, and checks the flagged ledgers match that sample's answer key
exactly. Exercises both endpoints together, the same round trip the frontend
will eventually do. Not part of `python3 -m unittest discover` (needs a live
server + the sibling data-synthesizer repo, same reasoning as
tests/verify_against_data_synthesizer.py). Run manually:

    python3 tests/test_api_manual.py
"""
import json
import mimetypes
import sys
import urllib.request
import uuid
from pathlib import Path

SAMPLE_DIR = Path(__file__).parent.parent.parent / "data-synthesizer" / "samples" / "trial_balance" / "05_coral_bay_infratech_pvt_ltd"
BASE_URL = "http://localhost:8000"


def post_json(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        BASE_URL + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def post_file(path: str, file_path: Path) -> dict:
    """Hand-rolled multipart/form-data upload (stdlib only -- this test file
    isn't part of the FastAPI exception api.py is)."""
    boundary = uuid.uuid4().hex
    content_type = mimetypes.guess_type(str(file_path))[0] or "text/csv"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode() + file_path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        BASE_URL + path, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def main():
    prior = post_file("/parse-trial-balance", SAMPLE_DIR / "prior_year_closing_trial_balance.csv")
    current = post_file("/parse-trial-balance", SAMPLE_DIR / "current_year_opening_trial_balance.csv")
    answer_key = json.loads((SAMPLE_DIR / "answer_key.json").read_text())
    expected_flagged_names = {e["ledger_name"] for e in answer_key["injected_errors"]}

    results = post_json("/run-checks", {
        "prior_year_trial_balance": prior,
        "current_year_trial_balance": current,
    })

    flagged_names = {r["source_reference"]["ledger"] for r in results if r["status"] == "flagged"}

    print(f"Ledgers parsed: prior={len(prior['ledgers'])}, current={len(current['ledgers'])}")
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
        print("PASS: full round trip (parse-trial-balance -> run-checks) matches the answer key.")
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
