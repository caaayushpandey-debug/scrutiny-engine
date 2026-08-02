"""Minimal local HTTP API wrapping check #1 (opening_balance_vs_prior_year_closing)
so the frontend can call it directly, instead of only being usable via the CLI.

This is the first dependency on anything outside the standard library in this
project (FastAPI + uvicorn, see requirements.txt) -- a deliberate, explicit
exception to the "standard library only" convention in CLAUDE.md, since
hand-rolling HTTP routing/JSON validation with stdlib http.server would not be
a reasonable use of effort for what is, by design, a real HTTP service meant
to be called over the network.

Run locally:
    ./venv/bin/uvicorn api:app --reload --port 8000

Endpoints:
- POST /run-checks, wrapping check #1 specifically -- this is not a generic
  "run any check by ID" dispatcher. If/when more checks are added, revisit
  whether this should become registry-driven (see coordinator.py /
  checks/registry.py) rather than hardcoded to one check.
- POST /parse-trial-balance, a raw-CSV-upload -> {"ledgers": [...]}
  preprocessing step (see trial_balance_csv_parser.py for the tolerant
  parsing logic), so the frontend doesn't have to parse Trial Balance CSVs
  itself before calling /run-checks.
- POST /parse-tally-xml, a raw-Tally-XML-upload -> structured TallyData
  ({"ledgers": [...], "vouchers": [...]}) preprocessing step (see
  tally_xml_parser.py). Not yet wired to any check endpoint the way
  /parse-trial-balance feeds /run-checks -- checks/suspense_account_scrutiny.py,
  the check actually validated against this data, has no HTTP endpoint yet.
"""
from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from checks.opening_balance_vs_prior_year_closing import DEFAULT_TOLERANCE, run_check
from schemas.trial_balance import LedgerBalance, TrialBalance
from tally_xml_parser import TallyXmlParseError, parse_tally_xml_data
from trial_balance_csv_parser import TrialBalanceParseError, parse_trial_balance_csv

app = FastAPI(
    title="AI Scrutiny Engine API",
    description="Local API wrapping the scrutiny-engine's check modules.",
    version="0.1.0",
)


class LedgerBalanceIn(BaseModel):
    name: str = Field(..., description="Ledger name, e.g. 'HDFC Bank Current A/c'")
    group: str = Field(..., description="Accounting group, e.g. 'Bank Accounts'")
    # Decimal, not float: money values must round-trip exactly (see
    # CLAUDE.md "Money values are handled with decimal.Decimal, never
    # float"). Pass debit/credit as JSON strings (e.g. "45200000.00") to
    # guarantee exact precision -- plain JSON numbers are technically floats
    # over the wire and risk introducing binary floating-point noise before
    # Pydantic ever sees them.
    debit: Decimal = Field(..., description="Debit amount as a string, e.g. \"45200000.00\"")
    credit: Decimal = Field(..., description="Credit amount as a string, e.g. \"0.00\"")


class TrialBalanceIn(BaseModel):
    ledgers: List[LedgerBalanceIn]

    def to_domain(self) -> TrialBalance:
        return TrialBalance(ledgers=[
            LedgerBalance(name=l.name, group=l.group, debit=l.debit, credit=l.credit)
            for l in self.ledgers
        ])


class RunChecksRequest(BaseModel):
    prior_year_trial_balance: TrialBalanceIn
    current_year_trial_balance: TrialBalanceIn
    # Rupee tolerance for amount mismatches; defaults to the check's own
    # DEFAULT_TOLERANCE (Rs 1.00) if omitted.
    tolerance: Optional[Decimal] = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/run-checks")
def run_checks(request: RunChecksRequest) -> List[dict]:
    """Runs check #1 against the two supplied trial balances and returns its
    results in the standard CheckResult shape (CLAUDE.md HARD RULE #2/#6).
    """
    if not request.prior_year_trial_balance.ledgers and not request.current_year_trial_balance.ledgers:
        raise HTTPException(status_code=400, detail="Both trial balances are empty -- nothing to check.")

    tolerance = request.tolerance if request.tolerance is not None else DEFAULT_TOLERANCE

    results = run_check(
        prior_year_trial_balance=request.prior_year_trial_balance.to_domain(),
        current_year_trial_balance=request.current_year_trial_balance.to_domain(),
        tolerance=tolerance,
    )

    return [r.to_dict() for r in results]


@app.post("/parse-trial-balance")
async def parse_trial_balance(file: UploadFile = File(...)) -> dict:
    """Parses an uploaded Trial Balance CSV into the {"ledgers": [...]} shape
    /run-checks' prior_year_trial_balance / current_year_trial_balance expect
    (debit/credit returned as strings, to preserve exact Decimal precision the
    same way /run-checks' own request shape does). See
    trial_balance_csv_parser.py for exactly what CSV variations are
    tolerated and what gets rejected.
    """
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")  # -sig gracefully strips a BOM if present
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=422, detail=f"Could not decode '{file.filename}' as UTF-8 text: {e}")

    try:
        trial_balance = parse_trial_balance_csv(text)
    except TrialBalanceParseError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {
        "ledgers": [
            {"name": l.name, "group": l.group, "debit": str(l.debit), "credit": str(l.credit)}
            for l in trial_balance.ledgers
        ]
    }


@app.post("/parse-tally-xml")
async def parse_tally_xml(file: UploadFile = File(...)) -> dict:
    """Parses an uploaded Tally XML export into the full structured
    TallyData shape -- every ledger master (name, parent, opening_balance)
    and every voucher (voucher_number, date, narration, legs), not just a
    collapsed trial balance. debit/credit-equivalent amounts are returned as
    strings, same Decimal-precision reasoning as /parse-trial-balance. Takes
    raw bytes from the upload directly (no text-decode step) -- Tally XML
    declares its own encoding, see tally_xml_parser.py's module docstring.
    """
    raw = await file.read()
    try:
        tally_data = parse_tally_xml_data(raw)
    except TallyXmlParseError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {
        "ledgers": [
            {"name": m.name, "parent": m.parent, "opening_balance": str(m.opening_balance)}
            for m in tally_data.ledgers.values()
        ],
        "vouchers": [
            {
                "vch_type": v.vch_type,
                "voucher_number": v.voucher_number,
                "date": v.date,
                "narration": v.narration,
                "legs": [
                    {"ledger_name": leg.ledger_name, "is_debit": leg.is_debit, "amount": str(leg.amount)}
                    for leg in v.legs
                ],
            }
            for v in tally_data.vouchers
        ],
    }
