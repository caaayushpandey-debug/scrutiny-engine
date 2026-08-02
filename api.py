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

Only one endpoint exists for now (POST /run-checks), wrapping check #1
specifically -- this is not a generic "run any check by ID" dispatcher. If/when
more checks are added, revisit whether this should become registry-driven
(see coordinator.py / checks/registry.py) rather than hardcoded to one check.
"""
from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from checks.opening_balance_vs_prior_year_closing import DEFAULT_TOLERANCE, run_check
from schemas.trial_balance import LedgerBalance, TrialBalance

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
