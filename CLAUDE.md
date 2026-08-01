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
  `schemas/trial_balance.py` (`LedgerBalance`, `TrialBalance`) is fully
  defined. The rest (`gstr1.py`, `gstr2.py`, `gstr3b.py`, `tds_return.py`,
  `form_26as.py`, `pf_esic_challan.py`, `bank_statement.py`,
  `payroll_report.py`) are placeholders — flesh each out when the first
  check that consumes it is built. `schemas/enums.py` holds `DocumentScope`
  and `DocumentType`. `schemas/__init__.py` re-exports everything.
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
- A check module that imports sibling packages (i.e. any check built after
  the schemas/registry/coordinator layer landed) must be run as
  `python3 -m checks.<module_name> ...`, not as a bare script path — see
  the "CLI usage" note at the top of
  `checks/opening_balance_vs_prior_year_closing.py` for why.

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
  (`version_scoped`).
