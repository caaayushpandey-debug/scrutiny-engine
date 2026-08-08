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
  checks/registry.py) rather than hardcoded to one check. Accepts EITHER the
  original raw-payload body (prior_year_trial_balance +
  current_year_trial_balance, unchanged) OR a client_id + fy + version_id
  reference (added 2026-08-06, feature/postgres-data-layer) that reads both
  trial balances from Postgres via checks/
  opening_balance_vs_prior_year_closing.run_check_from_db instead -- see
  RunChecksRequest's own docstring and CLAUDE.md's "Postgres data layer"
  section. Never both, never neither -- a request supplying fields from both
  modes, or neither, is rejected with 422 by RunChecksRequest's own
  validator before the handler runs at all.
- POST /store-trial-balance (added 2026-08-06, feature/postgres-data-layer)
  -- persists an already-parsed TrialBalance into Postgres, keyed by
  client_id + fy + scope[+ version_id], so a later /run-checks reference-mode
  call can read it back. A separate step from both parsing (the frontend
  calls /parse-trial-balance first) and running the check -- there is no
  browser-reachable way to write to Postgres directly (no client-side
  driver, unlike Firestore's own client SDK), so this endpoint is that write
  path. Upserts (safe to call again for the same version/scope, e.g. a
  "Replace File" correction). See StoreTrialBalanceRequest's own docstring.
- POST /parse-trial-balance, a raw-CSV-upload -> {"ledgers": [...]}
  preprocessing step (see trial_balance_csv_parser.py for the tolerant
  parsing logic), so the frontend doesn't have to parse Trial Balance CSVs
  itself before calling /run-checks.
- POST /parse-tally-xml, a raw-Tally-XML-upload -> structured TallyData
  ({"ledgers": [...], "vouchers": [...], "groups": [...]}) preprocessing step
  (see tally_xml_parser.py). "groups" is only the company's own custom
  <GROUP> masters (may be empty) -- see schemas/tally_data.py's
  TallyGroupMaster/resolve_top_level_group for why. Expects ONE
  self-contained file with both masters and vouchers (or masters alone) --
  for a real-world export split across separate masters-only and
  transactions-only files, use /parse-tally-xml-multi instead.
- POST /parse-tally-xml-multi, the same preprocessing step but for MULTIPLE
  Tally XML files merged into one TallyData (same response shape as
  /parse-tally-xml) -- confirmed against real user files (2026-08-06),
  Tally commonly splits a company's export into a masters-only file and a
  transactions-only file rather than one combined file, and a
  transactions-only file has no <LEDGER> masters of its own for
  /parse-tally-xml to validate against. See tally_xml_parser.py's "Split
  masters/transactions exports" for the merge logic and why <REPORTNAME> is
  never trusted to tell the two files apart.
- POST /run-suspense-check, wrapping checks/suspense_account_scrutiny.py
  specifically (same one-check-per-endpoint approach as /run-checks, not a
  registry-driven dispatcher). Accepts EITHER the same raw TallyData shape
  /parse-tally-xml returns (ledgers + vouchers, unchanged -- upload Tally
  XML to /parse-tally-xml, feed its response straight into
  /run-suspense-check) OR a client_id + fy + version_id reference (same
  dual-mode pattern as /run-checks above) that reads TallyData from
  Postgres via run_check_from_db instead. See TallyDataIn's own docstring.
- POST /store-tally-data (added 2026-08-06, feature/postgres-data-layer) --
  the tally_data counterpart to /store-trial-balance above: persists an
  already-parsed TallyData into Postgres, keyed by client_id + fy +
  version_id (always required -- TALLY_DATA has no prior-year scope). See
  StoreTallyDataRequest's own docstring.

File-parsing error responses (added 2026-08-08): all three upload endpoints
above (/parse-trial-balance, /parse-tally-xml, /parse-tally-xml-multi) return
a structured, plain-language error body on a parse failure -- see
parse_error_classification.classify_parse_error -- instead of a raw
exception message string. Every failure response's `detail` is a JSON object:
{"category": "...", "is_file_problem": bool, "message": "...",
 "suggested_fix": "...", "technical_detail": "..."}. Status code is 422 for
every recognized category (a problem WITH THE FILE) and 500 only for
"unknown" (a problem with THIS PROJECT'S code, not the file) -- see
tally_xml_parser.py's TallyXmlParseError subclass hierarchy for how a
failure gets sorted into a category in the first place.
"""
from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

from checks.opening_balance_vs_prior_year_closing import DEFAULT_TOLERANCE, run_check, run_check_from_db
from checks.suspense_account_scrutiny import run_check as run_suspense_account_scrutiny
from checks.suspense_account_scrutiny import run_check_from_db as run_suspense_check_from_db
from db.queries import (
    delete_version_scoped_data,
    get_tally_data,
    get_trial_balance,
    insert_tally_data,
    insert_trial_balance_ledgers,
)
from parse_error_classification import classify_parse_error
from schemas.enums import DocumentScope
from schemas.tally_data import TallyData, TallyGroupMaster, TallyLedgerMaster, TallyVoucher, TallyVoucherLeg
from schemas.trial_balance import LedgerBalance, TrialBalance
from tally_xml_parser import parse_tally_xml_data, parse_tally_xml_data_multi
from trial_balance_csv_parser import parse_trial_balance_csv

app = FastAPI(
    title="AI Scrutiny Engine API",
    description="Local API wrapping the scrutiny-engine's check modules.",
    version="0.1.0",
)

# The frontend (scrutiny-engine-frontend, a separate repo) runs on Vite's
# local dev server and calls this API directly from the browser -- with no
# CORS policy, the browser blocks every cross-origin request outright before
# it even reaches these routes. Both are local-only dev origins today (no
# deployed home for either service yet, see this repo's CLAUDE.md "Future
# integration").
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
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
    """Accepts EITHER the original raw-payload shape (prior_year_trial_balance
    + current_year_trial_balance, unchanged for backward compatibility) OR a
    client_id/fy/version_id reference into Postgres (see CLAUDE.md's
    "Postgres data layer" section) -- never both, never neither. This is the
    frontend-facing input-source swap this task adds; run_checks() below just
    dispatches on which mode was supplied, since checks/
    opening_balance_vs_prior_year_closing.py itself already exposes both
    run_check (payload) and run_check_from_db (reference) unchanged.
    """
    prior_year_trial_balance: Optional[TrialBalanceIn] = None
    current_year_trial_balance: Optional[TrialBalanceIn] = None
    client_id: Optional[str] = None
    fy: Optional[str] = None
    version_id: Optional[str] = None
    # Rupee tolerance for amount mismatches; defaults to the check's own
    # DEFAULT_TOLERANCE (Rs 1.00) if omitted. Applies to both input modes.
    tolerance: Optional[Decimal] = None

    @model_validator(mode="after")
    def _validate_exactly_one_input_mode(self) -> "RunChecksRequest":
        has_payload = self.prior_year_trial_balance is not None or self.current_year_trial_balance is not None
        has_reference = self.client_id is not None or self.fy is not None or self.version_id is not None

        if has_payload and has_reference:
            raise ValueError(
                "Provide either prior_year_trial_balance + current_year_trial_balance, "
                "or client_id + fy + version_id -- not both."
            )
        if has_payload:
            if self.prior_year_trial_balance is None or self.current_year_trial_balance is None:
                raise ValueError("prior_year_trial_balance and current_year_trial_balance are both required together.")
        elif has_reference:
            if not (self.client_id and self.fy and self.version_id):
                raise ValueError("client_id, fy, and version_id are all required together.")
        else:
            raise ValueError(
                "Provide either prior_year_trial_balance + current_year_trial_balance, "
                "or client_id + fy + version_id."
            )
        return self


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/run-checks")
def run_checks(request: RunChecksRequest) -> List[dict]:
    """Runs check #1 and returns its results in the standard CheckResult
    shape (CLAUDE.md HARD RULE #2/#6). Input source depends on which fields
    RunChecksRequest was given -- see its own docstring:
    - client_id/fy/version_id given -> run_check_from_db (Postgres). A
      "no data found" or "database unreachable" outcome is NOT an HTTP
      error -- it comes back as a normal 200 response with a
      status="insufficient_data" CheckResult, same contract every other
      check entry point already uses (see run_check_from_db's own
      docstring).
    - prior_year_trial_balance/current_year_trial_balance given -> run_check
      (raw payload), completely unchanged from before this task.
    """
    tolerance = request.tolerance if request.tolerance is not None else DEFAULT_TOLERANCE

    if request.client_id is not None:
        results = run_check_from_db(request.client_id, request.fy, request.version_id, tolerance=tolerance)
        return [r.to_dict() for r in results]

    if not request.prior_year_trial_balance.ledgers and not request.current_year_trial_balance.ledgers:
        raise HTTPException(status_code=400, detail="Both trial balances are empty -- nothing to check.")

    results = run_check(
        prior_year_trial_balance=request.prior_year_trial_balance.to_domain(),
        current_year_trial_balance=request.current_year_trial_balance.to_domain(),
        tolerance=tolerance,
    )

    return [r.to_dict() for r in results]


class StoreTrialBalanceRequest(BaseModel):
    """Persists an already-parsed TrialBalance into Postgres (see CLAUDE.md's
    "Postgres data layer" section) so a later /run-checks reference-mode call
    (client_id/fy/version_id) can read it back. This is a separate step from
    parsing (/parse-trial-balance) and from running the check (/run-checks) --
    the frontend calls /parse-trial-balance to get structured ledgers, then
    THIS endpoint to persist them, independently of whether/when the check is
    ever run. There is no browser-reachable way to write to Postgres directly
    (no client-side driver, unlike Firestore's client SDK), so this endpoint
    exists specifically to be that write path.
    """
    client_id: str
    fy: str
    scope: DocumentScope
    # Required when scope=version_scoped, ignored (forced to None) when
    # scope=period_scoped_prior_year -- see db/schema.sql's CHECK constraint
    # and insert_trial_balance_ledgers' own handling of this.
    version_id: Optional[str] = None
    ledgers: List[LedgerBalanceIn]

    @model_validator(mode="after")
    def _validate_version_id_matches_scope(self) -> "StoreTrialBalanceRequest":
        if self.scope == DocumentScope.VERSION_SCOPED and not self.version_id:
            raise ValueError("version_id is required when scope is version_scoped.")
        return self


@app.post("/store-trial-balance")
def store_trial_balance(request: StoreTrialBalanceRequest) -> dict:
    """Upserts request.ledgers into trial_balance_ledgers for
    (client_id, fy, scope[, version_id]) -- safe to call again for the same
    version/scope (e.g. a "Replace File" correction), which simply overwrites
    the prior rows rather than duplicating them.
    """
    trial_balance = TrialBalanceIn(ledgers=request.ledgers).to_domain()
    try:
        insert_trial_balance_ledgers(
            request.client_id, request.fy, request.scope, trial_balance, version_id=request.version_id
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Could not store trial balance in the database: {e}")

    return {"stored_ledgers": len(trial_balance.ledgers)}


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
        classified = classify_parse_error(e)
        raise HTTPException(status_code=classified.status_code, detail=classified.to_dict())

    try:
        trial_balance = parse_trial_balance_csv(text)
    except Exception as e:
        # Broad except is deliberate here, not just TrialBalanceParseError --
        # see classify_parse_error's docstring: anything NOT one of this
        # project's own recognized parse-error types still gets classified
        # (into the "unknown" catch-all) rather than leaking an unhandled
        # 500 traceback to the frontend.
        classified = classify_parse_error(e)
        raise HTTPException(status_code=classified.status_code, detail=classified.to_dict())

    return {
        "ledgers": [
            {"name": l.name, "group": l.group, "debit": str(l.debit), "credit": str(l.credit)}
            for l in trial_balance.ledgers
        ]
    }


class TallyLedgerMasterIn(BaseModel):
    name: str = Field(..., description="Ledger name, e.g. 'Axis Bank CC A/c'")
    parent: str = Field(..., description="Accounting group, e.g. 'Bank Accounts'")
    opening_balance: Decimal = Field(..., description="Opening balance as a string, e.g. \"100000.00\"")


class TallyVoucherLegIn(BaseModel):
    ledger_name: str
    is_debit: bool
    amount: Decimal = Field(..., description="Tally's own signed AMOUNT, as a string -- see tally_xml_parser.py's sign convention notes")


class TallyVoucherIn(BaseModel):
    vch_type: str
    voucher_number: str
    date: str
    narration: str = ""
    legs: List[TallyVoucherLegIn]


class TallyGroupMasterIn(BaseModel):
    name: str = Field(..., description="Group name, e.g. 'Overseas Debtors'")
    parent: str = Field(..., description="Parent group name (may be empty for a primary group emitted as a master)")


class TallyDataIn(BaseModel):
    """Accepts EITHER the original raw-payload shape (ledgers + vouchers,
    unchanged for backward compatibility -- exactly what /parse-tally-xml
    returns) OR a client_id/fy/version_id reference into Postgres -- never
    both, never neither. Same dual-mode pattern as RunChecksRequest above;
    see that class's docstring for the full rationale.

    `groups` (added 2026-08-08) is optional even in payload mode -- it only
    matters for persistence (/store-tally-data) and the visualizer's group
    hierarchy walk, not for the checks run here, so an older caller that
    omits it still validates and runs unchanged.
    """
    ledgers: Optional[List[TallyLedgerMasterIn]] = None
    vouchers: Optional[List[TallyVoucherIn]] = None
    groups: Optional[List[TallyGroupMasterIn]] = None
    client_id: Optional[str] = None
    fy: Optional[str] = None
    version_id: Optional[str] = None

    @model_validator(mode="after")
    def _validate_exactly_one_input_mode(self) -> "TallyDataIn":
        has_payload = self.ledgers is not None or self.vouchers is not None
        has_reference = self.client_id is not None or self.fy is not None or self.version_id is not None

        if has_payload and has_reference:
            raise ValueError("Provide either ledgers + vouchers, or client_id + fy + version_id -- not both.")
        if has_payload:
            if self.ledgers is None or self.vouchers is None:
                raise ValueError("ledgers and vouchers are both required together.")
        elif has_reference:
            if not (self.client_id and self.fy and self.version_id):
                raise ValueError("client_id, fy, and version_id are all required together.")
        else:
            raise ValueError("Provide either ledgers + vouchers, or client_id + fy + version_id.")
        return self

    def to_domain(self) -> TallyData:
        return TallyData(
            ledgers={m.name: TallyLedgerMaster(name=m.name, parent=m.parent, opening_balance=m.opening_balance) for m in self.ledgers},
            vouchers=[
                TallyVoucher(
                    vch_type=v.vch_type,
                    voucher_number=v.voucher_number,
                    date=v.date,
                    narration=v.narration,
                    legs=[TallyVoucherLeg(ledger_name=l.ledger_name, is_debit=l.is_debit, amount=l.amount) for l in v.legs],
                )
                for v in self.vouchers
            ],
            groups={g.name: TallyGroupMaster(name=g.name, parent=g.parent) for g in (self.groups or [])},
        )


@app.post("/run-suspense-check")
def run_suspense_check(request: TallyDataIn) -> List[dict]:
    """Runs checks/suspense_account_scrutiny.py and returns its results in
    the standard CheckResult shape (CLAUDE.md HARD RULE #2/#6). Input source
    depends on which fields TallyDataIn was given -- see its own docstring:
    - client_id/fy/version_id given -> run_check_from_db (Postgres). A
      "no data found" or "database unreachable" outcome comes back as a
      normal 200 response with a status="insufficient_data" CheckResult,
      same as /run-checks' reference path above.
    - ledgers/vouchers given -> run_suspense_account_scrutiny (raw payload,
      the same shape /parse-tally-xml returns), completely unchanged from
      before this task.
    """
    if request.client_id is not None:
        results = run_suspense_check_from_db(request.client_id, request.fy, request.version_id)
        return [r.to_dict() for r in results]

    if not request.ledgers and not request.vouchers:
        raise HTTPException(status_code=400, detail="Tally data is empty -- nothing to check.")

    results = run_suspense_account_scrutiny(request.to_domain())
    return [r.to_dict() for r in results]


class StoreTallyDataRequest(BaseModel):
    """Persists an already-parsed TallyData into Postgres, the tally_data
    counterpart to StoreTrialBalanceRequest above -- see that class's
    docstring for the full rationale (separate step from parsing and from
    running the check; no browser-reachable way to write to Postgres
    directly). TALLY_DATA has no prior-year scope (see schemas/enums.py's
    DEFAULT_SCOPE_BY_DOCUMENT_TYPE docstring), so version_id is always
    required here, unlike StoreTrialBalanceRequest's conditional one.
    """
    client_id: str
    fy: str
    version_id: str
    ledgers: List[TallyLedgerMasterIn]
    vouchers: List[TallyVoucherIn]
    # Optional/defaulted so a client that predates group persistence still
    # stores ledgers + vouchers unchanged -- see tally_groups in db/schema.sql.
    groups: List[TallyGroupMasterIn] = Field(default_factory=list)


@app.post("/store-tally-data")
def store_tally_data(request: StoreTallyDataRequest) -> dict:
    """Upserts request.ledgers/vouchers/groups into tally_ledgers/
    tally_vouchers/tally_voucher_legs/tally_groups for (client_id, fy,
    version_id) -- safe to call again for the same version (e.g. a "Replace
    File" correction).
    """
    tally_data = TallyDataIn(
        ledgers=request.ledgers, vouchers=request.vouchers, groups=request.groups
    ).to_domain()
    try:
        insert_tally_data(request.client_id, request.fy, request.version_id, tally_data)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Could not store Tally data in the database: {e}")

    return {
        "stored_ledgers": len(tally_data.ledgers),
        "stored_vouchers": len(tally_data.vouchers),
        "stored_groups": len(tally_data.groups),
    }


def _tally_data_to_response(tally_data: TallyData) -> dict:
    """Shared response shape for /parse-tally-xml and /parse-tally-xml-multi
    -- debit/credit-equivalent amounts are returned as strings, same
    Decimal-precision reasoning as /parse-trial-balance.
    """
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
        "groups": [
            {"name": g.name, "parent": g.parent}
            for g in tally_data.groups.values()
        ],
    }


@app.post("/parse-tally-xml")
async def parse_tally_xml(file: UploadFile = File(...)) -> dict:
    """Parses an uploaded Tally XML export into the full structured
    TallyData shape -- every ledger master (name, parent, opening_balance)
    and every voucher (voucher_number, date, narration, legs), not just a
    collapsed trial balance. Takes raw bytes from the upload directly (no
    text-decode step) -- tally_xml_parser.py sniffs the encoding itself
    (real exports are commonly UTF-16 with a BOM, not UTF-8).
    """
    raw = await file.read()
    try:
        tally_data = parse_tally_xml_data(raw)
    except Exception as e:
        # Broad except is deliberate here, not just TallyXmlParseError -- see
        # classify_parse_error's docstring: anything NOT one of this
        # project's own recognized parse-error types still gets classified
        # (into the "unknown" catch-all) rather than leaking an unhandled
        # 500 traceback to the frontend.
        classified = classify_parse_error(e)
        raise HTTPException(status_code=classified.status_code, detail=classified.to_dict())

    return _tally_data_to_response(tally_data)


@app.post("/parse-tally-xml-multi")
async def parse_tally_xml_multi(files: List[UploadFile] = File(...)) -> dict:
    """Parses and merges MULTIPLE Tally XML files into one structured
    TallyData -- for a real-world export split across a masters-only file
    (<GROUP>/<LEDGER>) and a transactions-only file (<VOUCHER>), which
    /parse-tally-xml (single file) can't accept since a transactions-only
    file has no <LEDGER> masters of its own. Which file is "masters" vs
    "transactions" is never assumed from filename or <REPORTNAME> (both can
    carry the same value regardless of actual content) -- content alone
    (which elements are actually present) determines what each file
    contributes, and the files can be supplied in any order. Same response
    shape as /parse-tally-xml. See tally_xml_parser.py's
    parse_tally_xml_data_multi / "Split masters/transactions exports".
    """
    raw_files = [await f.read() for f in files]
    try:
        tally_data = parse_tally_xml_data_multi(raw_files)
    except Exception as e:
        # See /parse-tally-xml's identical comment above.
        classified = classify_parse_error(e)
        raise HTTPException(status_code=classified.status_code, detail=classified.to_dict())

    return _tally_data_to_response(tally_data)


class VersionParsedDataRequest(BaseModel):
    """Reference into the parsed data stored for one (client_id, fy,
    version_id) -- the read counterpart to /store-tally-data and
    /store-trial-balance. Added 2026-08-08 so the frontend's Tally Data
    Visualizer can render a version's full parsed dataset by fetching it back
    FROM Postgres, instead of the frontend embedding a ~1MB copy of it in
    every Firestore version document (which broke uploads of large real
    files -- the embedded copy alone exceeded Firestore's 1MB/doc limit). The
    version doc now keeps only a lightweight summary; this endpoint serves the
    real thing on demand.
    """
    client_id: str
    fy: str
    version_id: str


@app.post("/version-parsed-data")
def version_parsed_data(request: VersionParsedDataRequest) -> dict:
    """Returns whichever parsed dataset(s) exist for this version, keyed by
    kind so the frontend can build its ParsedFileData union directly:
    - "tally_xml": the full {ledgers, vouchers, groups} shape (same as
      /parse-tally-xml), or null if this version stored no Tally data.
    - "trial_balance_csv": {ledgers: [...]} (same shape as
      /parse-trial-balance), or null if this version stored no version-scoped
      trial balance.
    A version can have neither, one, or both. An empty (never-stored) version
    returns both as null rather than an error -- the caller decides what that
    means, matching the "no error, just empty" contract of the read layer.
    """
    try:
        tally_data = get_tally_data(request.client_id, request.fy, request.version_id)
        trial_balance = get_trial_balance(
            request.client_id, request.fy, DocumentScope.VERSION_SCOPED, request.version_id
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Could not read parsed data from the database: {e}")

    tally_present = bool(tally_data.ledgers) or bool(tally_data.vouchers)
    tb_present = bool(trial_balance.ledgers)

    return {
        "tally_xml": _tally_data_to_response(tally_data) if tally_present else None,
        "trial_balance_csv": (
            {
                "ledgers": [
                    {"name": l.name, "group": l.group, "debit": str(l.debit), "credit": str(l.credit)}
                    for l in trial_balance.ledgers
                ]
            }
            if tb_present
            else None
        ),
    }


class DeleteVersionDataRequest(BaseModel):
    """Reference to one version's worth of stored data to remove. Added
    2026-08-08 as the compensating-cleanup path for the frontend's upload
    ordering: parsed data is written to Postgres BEFORE files are uploaded to
    Storage / the Firestore version doc is written (so a committed version
    can never lack the queryable rows the visualizer + checks read). If a
    later step in that same upload fails, the frontend calls this to roll the
    Postgres rows back, so a failed upload leaves no orphaned data behind.
    """
    client_id: str
    fy: str
    version_id: str


@app.post("/delete-version-data")
def delete_version_data(request: DeleteVersionDataRequest) -> dict:
    """Deletes every version-scoped row for (client_id, fy, version_id) across
    all tables (see db.queries.delete_version_scoped_data). Idempotent -- a
    version with nothing stored simply reports zero deletions rather than
    erroring, so the frontend can call it unconditionally on a failed upload.
    """
    try:
        deleted = delete_version_scoped_data(request.client_id, request.fy, request.version_id)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Could not delete version data from the database: {e}")

    return {"deleted": deleted}
