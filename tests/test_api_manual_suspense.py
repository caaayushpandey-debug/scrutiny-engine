"""Manual, standalone full-stack smoke test for api.py's Tally XML endpoints
against a running server on localhost:8000 -- uploads each real
data-synthesizer Tally XML sample's raw tally_export.xml to
/parse-tally-xml, feeds the parsed TallyData straight into
/run-suspense-check, and checks the flagged results match that sample's
answer_key.json exactly (same assertions as
verify_suspense_account_scrutiny_against_data_synthesizer.py, but exercised
over HTTP instead of calling the Python functions directly -- proves the API
layer, not just the underlying modules). Not part of
`python3 -m unittest discover` (needs a live server + the sibling
data-synthesizer repo, same reasoning as tests/test_api_manual.py and
tests/verify_suspense_account_scrutiny_against_data_synthesizer.py). Run
manually:

    ./venv/bin/uvicorn api:app --port 8000 &
    python3 tests/test_api_manual_suspense.py
"""
import json
import mimetypes
import sys
import urllib.error
import urllib.request
import uuid
from decimal import Decimal
from pathlib import Path

SAMPLES_ROOT = Path(__file__).parent.parent.parent / "data-synthesizer" / "samples" / "tally_xml"
BASE_URL = "http://localhost:8000"
AMOUNT_EPSILON = Decimal("0.01")


def post_json(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        BASE_URL + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def post_file(path: str, file_path: Path) -> dict:
    """Hand-rolled multipart/form-data upload (stdlib only -- same as
    tests/test_api_manual.py's post_file)."""
    boundary = uuid.uuid4().hex
    content_type = mimetypes.guess_type(str(file_path))[0] or "application/xml"
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


def verify_company(company_dir: Path) -> list:
    """Returns a list of human-readable failure strings; empty means pass."""
    failures = []

    tally_data = post_file("/parse-tally-xml", company_dir / "tally_export.xml")
    results = post_json("/run-suspense-check", tally_data)
    answer_key = json.loads((company_dir / "answer_key.json").read_text())

    expected_by_voucher = {
        e["phantom_voucher_number"]: (e["ledger_name"], Decimal(str(e["delta"])).copy_abs())
        for e in answer_key["injected_errors"]
    }
    actual_flags = {
        r["source_reference"]["voucher_number"]: (r["source_reference"]["ledger"], Decimal(str(r["amount"])))
        for r in results
        if r["status"] == "flagged"
    }

    missed = set(expected_by_voucher) - set(actual_flags)
    for vn in sorted(missed):
        failures.append(f"MISSED expected error on '{expected_by_voucher[vn][0]}' (voucher {vn}) -- not flagged")

    unexpected = set(actual_flags) - set(expected_by_voucher)
    for vn in sorted(unexpected):
        failures.append(f"FALSE POSITIVE: voucher '{vn}' on '{actual_flags[vn][0]}' flagged but not in answer key")

    for vn, (expected_ledger, expected_delta) in expected_by_voucher.items():
        if vn not in actual_flags:
            continue
        actual_ledger, actual_amount = actual_flags[vn]
        if actual_ledger != expected_ledger:
            failures.append(f"LEDGER MISMATCH on voucher '{vn}': API returned '{actual_ledger}', answer key says '{expected_ledger}'")
        if (actual_amount - expected_delta).copy_abs() > AMOUNT_EPSILON:
            failures.append(f"AMOUNT MISMATCH on voucher '{vn}': API returned {actual_amount}, answer key says {expected_delta}")

    return failures


def main():
    if not SAMPLES_ROOT.exists():
        print(f"Samples root not found: {SAMPLES_ROOT}")
        sys.exit(2)

    try:
        urllib.request.urlopen(BASE_URL + "/health", timeout=2)
    except urllib.error.URLError:
        print(f"No server responding at {BASE_URL} -- start it with ./venv/bin/uvicorn api:app --port 8000")
        sys.exit(2)

    company_dirs = sorted(p for p in SAMPLES_ROOT.iterdir() if p.is_dir())
    any_failures = False
    for company_dir in company_dirs:
        failures = verify_company(company_dir)
        status = "FAIL" if failures else "PASS"
        print(f"[{status}] {company_dir.name}")
        for f in failures:
            print(f"    - {f}")
            any_failures = True

    print()
    if any_failures:
        print("Verification FAILED.")
        sys.exit(1)
    else:
        print(f"Verification PASSED across {len(company_dirs)} companies -- API layer matches the answer keys exactly.")


if __name__ == "__main__":
    main()
