# Project: AI Scrutiny Engine

## What this is
The Python backend logic for the CA/CPA audit automation tool's actual
scrutiny/reconciliation checks. Each check is an independent, testable module
that compares data sources (Tally exports, GST returns, TDS/26AS, bank
statements, payroll challans, etc.) and outputs a structured pass/flagged/
insufficient_data result per item examined.

## Relationship to other projects
This is a separate, standalone project with no shared code with either:
- `scrutiny-engine-frontend` (React/TypeScript) — that project's backend will
  eventually call these checks, but no interface/API contract between them
  exists yet. Do not assume shared conventions, dependencies, or language.
- `data-synthesizer` (Python) — generates the fake-but-structurally-realistic
  test data (with a known answer key of deliberately injected errors) that
  this project's checks are validated against. This project reads that
  project's *output format* (e.g. its trial balance CSV shape), but shares no
  code with it.

## ROLE
You are acting as a senior engineer specializing in financial data
reconciliation systems, building independent, testable Python check modules
for a CA/CPA audit tool.

## HARD RULES
1. Reconciliation/matching checks (comparing two numeric sources) must be
   deterministic, plain Python logic with explicit, commented
   tolerance/matching rules — never an LLM call deciding whether something
   matches.
2. Every check's output must follow this exact shape: `check_id`, `status`
   ("pass"/"flagged"/"insufficient_data"), `confidence_score` (0-1),
   `description` (plain language), `amount`, `source_reference` (ledger,
   voucher_number, date where applicable).
3. Every check file needs a docstring, explicit typed input expectations, and
   inline comments explaining any tolerance thresholds chosen.
4. Before calling any check "final," it must be tested against real sample
   data with a known answer key, and the check's output must be compared
   against that answer key programmatically — not just eyeballed.
5. Flag any assumption about Indian tax/audit law you're not fully certain
   about, rather than silently assuming.
6. Every "flagged" result must include a structured explanation, not just a
   one-line description. Flagged results carry these additional fields:
   - `finding`: the specific discrepancy in plain language, with exact
     numbers (what doesn't match, by how much).
   - `potential_implication`: what this kind of mismatch commonly indicates
     in a real audit context (e.g. unrecorded disposal, unauthorized
     adjustment, data migration error, a legitimate but undocumented
     write-off) — a professional CA's read on what this class of error could
     mean, not just "numbers don't match."
   - `recommended_manual_check`: the specific, concrete action a CA should
     take to resolve this (what document/record to check against, who to
     ask).
   - `why_correction_matters`: the downstream consequence of leaving this
     unresolved (e.g. undermines reliability of all subsequent account
     movement, may indicate a control weakness).
   `"pass"` and `"insufficient_data"` results only need the existing plain
   `description` field — this richer structure exists specifically because
   `"flagged"` is what a CA actually has to act on.
7. Every document type has exactly one of three scopes, and every check must
   declare its data requirements using them (`document_type` + `scope`),
   never a hardcoded file path:
   - `version_scoped`: tied to a specific client + FY + version — changes
     with every new Tally data upload (e.g. the current-period Trial
     Balance/Tally data for the version being scrutinized).
   - `period_scoped_external`: tied to a client + FY, uploaded once, reused
     across every version (e.g. GSTR1, GSTR2, GSTR3B, TDSReturn, Form26AS,
     PFESICChallan, BankStatement, PayrollReport).
   - `period_scoped_prior_year`: tied to a client + FY, uploaded once at FY
     setup (before V1 exists), reused across every version — specifically
     last year's audited closing Tally/Trial Balance data, which does not
     change as current-year versions are revised.
   See "Document scope model" below for the full rationale and how the
   coordinator resolves each scope.

## WORKING RULE
At the end of any task or meaningful chunk of work, always run git add, git
commit with a clear descriptive message, and git push.

## Conventions
- Python 3, standard library only (no external dependencies / no pip install
  required) unless a strong reason comes up later — same convention as
  `data-synthesizer`, and avoids repeating the environment-setup friction hit
  in the frontend project. Tests use the built-in `unittest` module, not
  pytest, for the same reason.
  - **Exception: `api.py`** (added 2026-08-02). FastAPI + uvicorn, installed
    into `./venv` (gitignored; recreate with `python3 -m venv venv && ./venv/bin/pip
    install -r requirements.txt`). This is the one deliberate exception —
    exposing a check over HTTP for the frontend to call is a real network
    service, not something worth hand-rolling on stdlib `http.server` for.
    Every check module underneath it stays stdlib-only; only the HTTP layer
    itself has this dependency.
- Money values are handled with `decimal.Decimal`, never `float`, to avoid
  floating-point rounding noise being confused with genuine discrepancies.
- `confidence_score` is 1.0 for every fully deterministic check (per HARD
  RULE #1, there is no probabilistic judgment for these) — reserved for
  future checks that may involve genuine uncertainty (e.g. fuzzy name
  matching, OCR-derived data).
- `"insufficient_data"` is reserved for genuine cases where the check itself
  cannot produce a result at all — e.g. a corrupted, missing, or unparseable
  input file. It is NOT used for a per-item finding like a missing ledger:
  that's a real audit-relevant finding a CA needs to act on, so it must be
  `"flagged"` (with the HARD RULE #6 structured explanation), not
  `"insufficient_data"`.
- [Fill in more conventions as they're established.]

## Document scope model
This is the shared data layer every check builds on (HARD RULE #7) — so no
check has to reinvent how it finds and reads its own input.

A client's audit work for a financial year has one prior-year closing
position, one set of external filed documents (GST returns, TDS/26AS, bank
statements, payroll), and potentially many *versions* of the current year's
Tally data as it gets revised over time. A check's data requirement needs to
say which of those three things it means, because "has the document been
provided" resolves completely differently for each:

- **`version_scoped`** documents are resolved against the *specific version*
  being scrutinized. A Trial Balance uploaded for version 2 does not satisfy
  a `version_scoped` requirement when version 3 is what's being checked.
- **`period_scoped_external`** and **`period_scoped_prior_year`** documents
  are resolved against the *FY as a whole*, independent of which version is
  being scrutinized — they're uploaded once and every version reuses the
  same copy.

`TrialBalance` is the one document type that legitimately appears in two
different scopes depending on role: this year's opening/current data is
`version_scoped`, while last year's audited closing balance is
`period_scoped_prior_year`. Every other document type currently defined has
exactly one scope (see `schemas/enums.py`'s `DEFAULT_SCOPE_BY_DOCUMENT_TYPE`).

Implementation, so a future session can find the pieces:
- `schemas/enums.py` — `DocumentScope`, `DocumentType`.
- `schemas/<document_type>.py` — one module per document type; see Structure.
- `checks/requirements.py` — `DataRequirement`, the shape a check declares
  its needs in (`role`, `document_type`, `scope`, `description`).
- `checks/registry.py` — `CHECK_REGISTRY`, mapping every check to its
  `DataRequirement`s. Add an entry here for every new check.
- `coordinator.py` — `AvailableDocuments` (what's been uploaded for a
  client/FY, as version-scoped-by-version-id and period-scoped sets) and
  `evaluate_check_readiness`/`evaluate_all_checks`, which report per check
  whether it `can_run` and, if not, exactly which `DataRequirement`(s) are
  `missing`. This only answers "is the data there yet" — it does not load
  documents or execute checks; that's each check's own `run_check`/
  `run_check_from_files`.

## Structure (update as it grows)
- `schemas/` — one module per document type, each independently importable.
  `schemas/trial_balance.py` (`LedgerBalance`, `TrialBalance`) and
  `schemas/tally_data.py` (`TallyLedgerMaster`, `TallyVoucher`,
  `TallyVoucherLeg`, `TallyData`) are fully defined. `TallyData` keeps full
  ledger-master + voucher-leg detail (unlike `TrialBalance`'s one-number-
  per-ledger shape) because a Tally ledger's closing balance isn't a single
  stored field — see `TallyData.closing_balance()` and `tally_xml_parser.py`.
  The rest (`gstr1.py`, `gstr2.py`, `gstr3b.py`, `tds_return.py`,
  `form_26as.py`, `pf_esic_challan.py`, `bank_statement.py`,
  `payroll_report.py`) are placeholders — flesh each out when the first
  check that consumes it is built. `schemas/enums.py` holds `DocumentScope`
  and `DocumentType` (`TALLY_DATA` added 2026-08-02, always
  `VERSION_SCOPED` — no check needs "last year's Tally data" as a distinct
  role the way `TRIAL_BALANCE` does). `schemas/__init__.py` re-exports
  everything.
- `checks/` — one module per check, each independently importable and
  independently testable. `checks/results.py` (`CheckResult`,
  `SourceReference`) and `checks/requirements.py` (`DataRequirement`) are
  shared infrastructure every check imports rather than redefining.
  `checks/registry.py` lists every check.
- `coordinator.py` — see "Document scope model" above.
- `tests/` — one test module per check/schema module, using hand-built
  fixtures for basic sanity coverage, plus real `data-synthesizer` output +
  `answer_key.json` for the HARD RULE #4 final validation
  (`verify_against_data_synthesizer.py` is the reusable harness for that;
  it's a standalone script, not part of `python3 -m unittest discover`).
  `test_api_manual.py` is the equivalent full-stack harness for `api.py` —
  also a standalone script (needs a running server), not part of
  `unittest discover`.
- `trial_balance_csv_parser.py` — tolerant CSV → `TrialBalance` parser used
  by `api.py`'s `/parse-trial-balance`, independent of and more lenient than
  `schemas/trial_balance.py`'s strict `TrialBalance.from_csv` (which stays
  as the canonical, exact-format parser other checks can rely on). See its
  own docstring for exactly what variations are tolerated.
- `tally_xml_parser.py` (added 2026-08-02) — Tally XML → `TallyData` /
  `TrialBalance` parser, matching the schema `data-synthesizer`'s Tally XML
  generator produces (see that project's
  `generators/tally_xml/generate_tally_xml.py` for the exact schema
  citation). Two entry points: `parse_tally_xml_data`/`parse_tally_xml_data_file`
  return a full `TallyData` (every ledger master + every voucher, untouched);
  `parse_tally_xml`/`parse_tally_xml_file` collapse that into a
  `TrialBalance` of permanent (balance-sheet) ledgers' *closing* balances,
  computed by summing every voucher leg against each ledger's opening
  balance — not read from any single stated field. Validates Tally's
  ISDEEMEDPOSITIVE/AMOUNT sign convention (rejects inconsistencies rather
  than trusting one field over the other) and that every voucher's legs sum
  to zero. Excludes Profit & Loss ledgers (Tally's own fixed primary-group
  list: Sales/Purchase Accounts, Direct/Indirect Incomes/Expenses) from the
  `TrialBalance` output, matching `opening_balance_vs_prior_year_closing.py`'s
  documented balance-sheet-only assumption.
  **KNOWN LIMITATION** (found while validating this against check #1, before
  the fix below): `opening_balance_vs_prior_year_closing.py` tests a
  same-point-in-time continuity fact (this year's *opening* should equal
  last year's *closing*). A `TrialBalance` built from a full year of
  realistic, uncorrelated trading vouchers via `parse_tally_xml` will
  legitimately differ from the opening balance for any active ledger — real
  business activity, not a discrepancy — and produces false positives on
  nearly every active ledger if fed into that check. Confirmed empirically:
  in the two *clean* (zero-injected-error) sample companies, 100% of
  eligible ledgers were false-flagged. `parse_tally_xml`/`parse_tally_xml_file`
  should only ever be fed a Tally file whose vouchers net to zero for the
  year (or no vouchers at all) — never a full year of real trading data;
  see the module's own "KNOWN LIMITATION" docstring section for the full
  writeup. `opening_balance_vs_prior_year_closing.py` itself was not
  changed and stays CSV/stated-balance-focused. This is why the Tally
  pipeline's actually-validated check is `suspense_account_scrutiny.py`
  (below), not that one.
  Unit tests: `tests/test_tally_xml_parser.py` (22 tests: sign-convention
  math, P&L filtering, every rejection path, `TallyData` field/method
  coverage).
- A check module that imports sibling packages (i.e. any check built after
  the schemas/registry/coordinator layer landed) must be run as
  `python3 -m checks.<module_name> ...`, not as a bare script path — see
  the "CLI usage" note at the top of
  `checks/opening_balance_vs_prior_year_closing.py` for why.
- `api.py` — minimal FastAPI service exposing checks over HTTP (see
  "Conventions" exception above). Two endpoints so far:
  - `POST /run-checks` — wraps check #1 specifically (not a generic
    registry-driven dispatcher yet). Request body: `prior_year_trial_balance`
    and `current_year_trial_balance`, each `{"ledgers": [{"name", "group",
    "debit", "credit"}, ...]}` — pass `debit`/`credit` as JSON strings (e.g.
    `"45200000.00"`) to guarantee exact `Decimal` precision, not plain JSON
    numbers. Optional `tolerance` (string, rupees) overrides the check's
    default. Returns a JSON array of `CheckResult.to_dict()` — the same shape
    `checks/opening_balance_vs_prior_year_closing.py`'s own `main()` prints.
  - `POST /parse-trial-balance` — raw CSV file upload (multipart/form-data,
    field name `file`) → the same `{"ledgers": [...]}` shape `/run-checks`
    consumes (debit/credit returned as strings, same precision reasoning).
    Parsing logic lives in `trial_balance_csv_parser.py` (see "Structure"),
    tolerant of real-world header-name/order/currency-symbol variations but
    never silently guessing — see that module's docstring for exactly what's
    accepted vs rejected (422 with a specific reason: which required column
    couldn't be found and what the actual headers were, or which row/ledger
    had an unparseable amount).
  - `GET /health` for a basic liveness check. Auto-generated interactive docs
    at `/docs` once running.
  Run locally: `./venv/bin/uvicorn api:app --reload --port 8000`.
  Tested (2026-08-02): `tests/test_api_manual.py` uploads a real
  data-synthesizer sample's raw CSVs (the 4-error company) to
  `/parse-trial-balance`, feeds the parsed ledgers straight into
  `/run-checks`, and confirms the flagged ledgers exactly match that
  sample's answer key — a genuine full-stack round trip through both
  endpoints, not synthetic ledger JSON. Same standard this project already
  held the check module itself to (HARD RULE #4), now proven through the
  HTTP layer too. `trial_balance_csv_parser.py` additionally has its own
  unit tests (`tests/test_trial_balance_csv_parser.py`) covering header
  aliases, currency/comma/parentheses number cleanup, and every rejection
  path, plus a verified byte-for-byte equivalence check against the
  existing strict `TrialBalance.from_csv` for all 10 real sample CSVs (5
  companies × prior/current) — proves the tolerant parser doesn't change
  behavior for the canonical format, only adds tolerance for variations.
  Also manually verified on `/run-checks`: empty-input 400, malformed-input
  422 with field-level detail, custom `tolerance` override, and Decimal
  precision round-tripping correctly through JSON.

## Checks status
- `opening_balance_vs_prior_year_closing.py` — **FINAL.** Validated against 5
  real data-synthesizer sample companies (2 clean, 3 with 2/3/4 injected
  errors); every injected error flagged, no false positives, amounts match
  the answer key to the paisa, and every flagged result carries all four
  HARD RULE #6 structured explanation fields, non-empty. Missing-ledger
  cases are `"flagged"` (not `"insufficient_data"`). Re-validated after
  being migrated onto the shared schemas/registry/coordinator data layer —
  see the module's own docstring for details. Declares two
  `DataRequirement`s: prior-year trial balance
  (`period_scoped_prior_year`) and current-year trial balance
  (`version_scoped`). CSV/`TrialBalance`-only — deliberately NOT run against
  Tally XML's voucher-derived data; see `tally_xml_parser.py`'s "KNOWN
  LIMITATION" entry above for why that combination doesn't make sense.
- `suspense_account_scrutiny.py` (added 2026-08-02) — **FINAL.** Consumes
  `TallyData` (ledger masters + vouchers) directly, not `TrialBalance` — no
  prior-year document needed, since it only reasons about postings within
  one Tally file. Flags every voucher that posts through a designated
  Suspense ledger (name-matched, see `SUSPENSE_LEDGER_NAMES` — an
  ASSUMPTION per HARD RULE #5), one flagged result per (voucher,
  non-Suspense leg). Built specifically because `data-synthesizer`'s Tally
  XML generator's injected errors are, by construction, an extra Journal
  voucher routed through "Suspense Account" — real CA audit practice
  already always scrutinizes suspense-account activity, so this is a
  genuine, deterministic (HARD RULE #1) check, not a test-data-specific
  hack. Validated by
  `tests/verify_suspense_account_scrutiny_against_data_synthesizer.py`
  against all 5 real Tally XML sample companies: every injected error
  flagged by its phantom voucher number, no false positives, amounts match
  the answer key to the paisa, all four HARD RULE #6 fields present on
  every flagged result. Declares one `DataRequirement`: current-year Tally
  data (`version_scoped`). Unit tests:
  `tests/test_suspense_account_scrutiny.py` (7 tests, hand-built `TallyData`
  fixtures).
