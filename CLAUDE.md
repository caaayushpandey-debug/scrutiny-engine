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

## Structure (update as it grows)
- `checks/` — one module per check, each independently importable and
  independently testable.
- `tests/` — one test module per check, using hand-built fixtures for basic
  sanity coverage, plus real `data-synthesizer` output + `answer_key.json`
  for the HARD RULE #4 final validation (`verify_against_data_synthesizer.py`
  is the reusable harness for that; it's a standalone script, not part of
  `python3 -m unittest discover`).

## Checks status
- `opening_balance_vs_prior_year_closing.py` — **FINAL.** Validated against 5
  real data-synthesizer sample companies (2 clean, 3 with 2/3/4 injected
  errors); every injected error flagged, no false positives, amounts match
  the answer key to the paisa, and every flagged result carries all four
  HARD RULE #6 structured explanation fields, non-empty. Missing-ledger
  cases are `"flagged"` (not `"insufficient_data"`) — see the module's own
  docstring for details.
