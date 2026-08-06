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

## Postgres data layer (design, added 2026-08-06 — schema/rationale written before any code, per explicit instruction)
Every check so far (`opening_balance_vs_prior_year_closing.py`,
`suspense_account_scrutiny.py`) takes its input as an already-parsed
in-memory `TrialBalance`/`TallyData` object, loaded straight from a raw CSV
or Tally XML file passed on the CLI or over `/run-checks`/`/run-suspense-check`.
That's fine for validating a check's logic against `data-synthesizer` output,
but there's no persisted, queryable store of a client's financial data across
FYs/versions — this section designs one, on `feature/postgres-data-layer`,
worked out and documented here **before** any table or Python code exists.

### Why Postgres, and why now
The frontend (`scrutiny-engine-frontend`) already persists client/version/FY
metadata in Firestore, but the actual parsed financial data
(`ParsedFileData`, e.g. Tally ledgers/vouchers) only ever lives as a JSON
blob attached to a file's metadata — there's no relational store a check can
query by `(client_id, fy, version_id)` without the frontend re-shipping the
whole blob over HTTP every time. Postgres is introduced here, in this
project, as that queryable store for the checks that need one — a relational
database is the right fit specifically because every document type here has
a natural tabular shape (rows of ledgers, rows of voucher legs) and because
Postgres Row-Level Security (see below) gives a second, database-enforced
tenant-isolation guarantee that a JSON blob store can't. This does not
replace Firestore for the frontend's own client/version/audit-trail
bookkeeping — see that project's CLAUDE.md — it is a new store scoped to
this project's own document data.

### Scope: which document types get tables now
Per HARD RULE #7's three document types already in `schemas/`, this first
pass only covers the two document types that have a real, validated check
consuming them today (`opening_balance_vs_prior_year_closing.py` needs
`TrialBalance`; `suspense_account_scrutiny.py` needs `TallyData`) — the eight
placeholder schemas (`gstr1.py`, `bank_statement.py`, etc.) get tables when
the first check that actually consumes them is built, not speculatively now.

### Tables
One table per dataclass in `schemas/trial_balance.py` and
`schemas/tally_data.py`, not one table per document type — `TallyData`
itself has three levels of nested structure (ledger masters, vouchers,
voucher legs) that don't collapse into a single table without either
duplicating leg rows across ledgers or losing per-leg detail, so it becomes
three tables. Every table carries `client_id`, `fy`, and `version_id`
(`TEXT`, matching the frontend's own id shapes — Firestore doc ids and
`V1`/`V2`-style version labels, not integers) so a check's data access
call can filter to exactly the scope it needs, and every table has a
composite `(client_id, fy)` index at minimum (see "Indexes" below) since
that's the coordinator's own `AvailableDocuments` granularity (see "Document
scope model" above) and the shape every data-access function's WHERE clause
uses.

- **`trial_balance_ledgers`** — `schemas/trial_balance.py`'s `LedgerBalance`.
  One row per ledger. Also carries a `scope` column
  (`'version_scoped'` | `'period_scoped_prior_year'`) because `TrialBalance`
  is the one document type used in two different scope roles (see "Document
  scope model" above) — a prior-year closing balance and a current-year
  opening balance for the same client/FY/ledger name are two distinct rows,
  not one. `version_id` is `NOT NULL` when `scope = 'version_scoped'` and
  `NULL` when `scope = 'period_scoped_prior_year'` (enforced by a `CHECK`
  constraint) — a prior-year closing balance is uploaded once per FY, not
  per version, matching `AvailableDocuments.period_scoped_documents` in
  `coordinator.py`.
- **`tally_ledgers`** — `schemas/tally_data.py`'s `TallyLedgerMaster`. One
  row per ledger master. `TALLY_DATA` has no prior-year role (see
  `schemas/enums.py`'s `DEFAULT_SCOPE_BY_DOCUMENT_TYPE` docstring), so
  `version_id` is always `NOT NULL` here — no scope column needed.
- **`tally_vouchers`** — `schemas/tally_data.py`'s `TallyVoucher`, minus
  `legs` (its own table below). One row per voucher.
- **`tally_voucher_legs`** — `schemas/tally_data.py`'s `TallyVoucherLeg`.
  One row per leg, foreign-keyed to `tally_vouchers.id`
  (`ON DELETE CASCADE`), but **also** carries its own denormalized
  `client_id`/`fy`/`version_id` rather than relying on a join up to
  `tally_vouchers` for those — this is deliberate, not an oversight: the RLS
  policy below (per-table `client_id` filter) needs a column to filter on
  directly, and a policy that required joining to a parent table to
  determine visibility would be both slower and a weaker guarantee (the join
  path itself becomes something a future schema change could accidentally
  break). `leg_order` (int) preserves the original leg order within a
  voucher, since `dataclass` field order in `TallyVoucher.legs` currently
  carries no explicit sequence number.

Custom Tally group masters (`schemas/tally_data.py`'s `TallyGroupMaster`,
used by the frontend's `TallyDataVisualizer.tsx` group-hierarchy walk, see
`scrutiny-engine-frontend`'s CLAUDE.md) are **not** given a table in this
pass — no check here reads `TallyData.groups` today (only
`resolve_top_level_group`, which is frontend/visualizer-only classification
logic, not a check). Add `tally_groups` when a check needs it.

### Indexes
Every table gets a composite `(client_id, fy)` btree index (the coordinator/
data-access query shape) plus a uniqueness constraint preventing the same
logical row from being loaded twice:
- `trial_balance_ledgers`: two **partial** unique indexes (not one plain
  `UNIQUE` including `version_id`), because `version_id` is `NULL` for every
  `period_scoped_prior_year` row and Postgres treats `NULL <> NULL` in a
  unique index — a plain `UNIQUE(client_id, fy, version_id, ledger_name)`
  would silently allow duplicate prior-year rows for the same ledger. Split
  into `UNIQUE (client_id, fy, version_id, ledger_name) WHERE scope =
  'version_scoped'` and `UNIQUE (client_id, fy, ledger_name) WHERE scope =
  'period_scoped_prior_year'` instead.
- `tally_ledgers`: `UNIQUE (client_id, fy, version_id, ledger_name)`.
- `tally_vouchers`: `UNIQUE (client_id, fy, version_id, voucher_number)` —
  matches how checks already key a voucher (`suspense_account_scrutiny.py`'s
  `SourceReference.voucher_number`, and every `data-synthesizer` answer key's
  `phantom_voucher_number`).
- `tally_voucher_legs`: no uniqueness constraint beyond the `voucher_id` FK
  — a voucher can legitimately have two legs against the same ledger (e.g.
  two separate debit postings to the same ledger within one voucher), so
  there is no natural leg-level unique key beyond the surrogate `id`.

### Row-Level Security (second enforcement layer)
Postgres RLS is added on top of the data access layer's own
`WHERE client_id = ...` filtering (defense in depth — see
`scrutiny-engine-frontend`'s CLAUDE.md "wide open Firestore rules" item for
what happens when application-level filtering is the *only* layer and
someone bypasses the application). Mechanism:
- A dedicated, non-superuser, non-table-owning Postgres role
  (`scrutiny_app`) is what the data access layer connects as — migrations/
  DDL run as the owning role instead. This matters because RLS is silently
  bypassed for a table's owner and for superusers unless
  `FORCE ROW LEVEL SECURITY` is also set; using a separate, lower-privilege
  role for all application queries means the policies below apply
  unconditionally, without needing `FORCE` at all.
- Every table has `ROW LEVEL SECURITY` enabled and one policy:
  `USING (client_id = current_setting('app.current_client_id', true))`.
- The data access layer issues `SELECT set_config('app.current_client_id',
  <id>, true)` as the first statement of every transaction, scoped to that
  transaction only (the third `set_config` argument, `is_local`, gives the
  same transaction-only scoping `SET LOCAL` would -- `set_config()` is used
  instead of a literal `SET LOCAL ... = <id>` specifically because `SET` is
  a utility statement that doesn't accept a bind parameter for its value;
  `set_config()` is a normal function call that does) so a pooled/reused
  connection can never leak one request's client scope into the next. If
  that session variable is
  unset, `current_setting(..., true)` returns `NULL`, and `client_id = NULL`
  is never true for any real row — so a caller that forgets to scope a
  connection gets zero rows back, not another client's data.
- This is a second, database-enforced layer specifically so that a bug in
  the data access layer's own `WHERE` clause (or, later, a hypothetical
  direct query that bypasses the data access layer despite the "only code
  allowed to query these tables directly" rule) fails closed rather than
  leaking cross-client data.

### Local setup (no cloud dependency, matching the frontend's Firebase
emulator approach)
Originally planned as Docker (matching `scrutiny-engine-frontend`'s "free,
local, no cloud dependency" emulator convention), but this machine had
neither Docker nor Homebrew installed when this branch started — the user
chose to install Homebrew and run Postgres natively via `brew services`
instead of blocking on a Docker Desktop install
(`docker-compose.yml` is still written and kept in the repo for future
portability/CI, just not what local dev actually runs against today):
1. `brew install postgresql@16`, then `brew services start postgresql@16`
   (registers it as a login-item background service — survives reboots,
   matches how the frontend's Firebase emulators are a separate
   long-running local process alongside `npm run dev`).
2. `createdb scrutiny_engine` (one-time, run as the Homebrew-installed
   admin role — on macOS this is your own macOS username by default, no
   password needed for local trust-auth connections).
3. `psql -d scrutiny_engine -f db/schema.sql` — creates all four tables,
   indexes, the `scrutiny_app` role, and RLS policies. Idempotent-ish (uses
   `CREATE TABLE IF NOT EXISTS` / `DO $$ ... EXCEPTION WHEN duplicate_object`
   guards for the role) so it's safe to re-run.
4. `./venv/bin/pip install -r requirements.txt` — adds `psycopg[binary]` as
   a new dependency (see "Conventions" — this is the same kind of deliberate
   exception `api.py`'s FastAPI dependency already is: talking to a real
   network service, here Postgres instead of HTTP, isn't something to
   hand-roll on stdlib `socket`). Every check module itself stays
   stdlib-only; only `db/connection.py` and `db/queries.py` import
   `psycopg`.
5. `python3 -m db.load_sample_data` — one-time loader populating
   `scrutiny_engine` from the sibling `data-synthesizer` repo's existing
   sample output (`samples/trial_balance/*` and `samples/tally_xml/*`), so
   the migrated checks have real, answer-key-backed data to read for HARD
   RULE #4 verification. See `db/load_sample_data.py`'s own docstring for
   the `client_id`/`fy`/`version_id` values it assigns to each sample
   company (there's no real client/FY/version registry in this project yet
   — that lives in the frontend's Firestore, which this loader does not
   read from).
Connection string: `postgresql://localhost/scrutiny_engine` for the owning/
migration role (peer/trust auth, no password, standard for a native
Homebrew Postgres on macOS); the app role's own DSN is
`postgresql://scrutiny_app@localhost/scrutiny_engine` (also no password
locally — see `db/schema.sql`'s role creation for the exact grant; a real
password/`.pgpass`/env-var secret would be needed before this ever runs
against a shared, non-local Postgres instance, which is out of scope for
this local-only pass).

### Data access layer (`db/`)
`db/connection.py` and `db/queries.py` are the **only** code in this project
allowed to issue a SQL query against these tables — every check keeps
consuming plain `TrialBalance`/`TallyData` objects exactly as before (see
"Migrating the checks" below), never a `psycopg` cursor or raw SQL string.
- `db/connection.py` — `client_scoped_connection(client_id)`, a context
  manager that opens a connection as `scrutiny_app`, issues
  `SET LOCAL app.current_client_id = <id>` inside a transaction, yields a
  cursor, and commits/rolls back on exit. This is the one place
  `SET LOCAL app.current_client_id` is ever issued.
- `db/queries.py` — one function per table/document type, per the task's
  own naming: `get_trial_balance(client_id, fy, scope, version_id=None) ->
  TrialBalance`, `get_tally_ledgers(client_id, fy, version_id) ->
  Dict[str, TallyLedgerMaster]`, `get_tally_vouchers(client_id, fy,
  version_id) -> List[TallyVoucher]` (assembles legs via a second query
  keyed on the fetched voucher ids, ordered by `leg_order`), and
  `get_tally_data(client_id, fy, version_id) -> TallyData` (a thin composer
  calling the previous two and assembling the dataclass checks actually
  need — still doesn't issue SQL directly itself, so the "only these two
  modules query the tables" rule holds). Every function returns the exact
  same dataclasses `schemas/` already defines — a check migrated onto this
  layer gets back the identical object shape it got from
  `TrialBalance.from_csv`/`parse_tally_xml_data_file` before.

### Migrating the checks (input source swap only, logic unchanged)
Per explicit instruction, `run_check()` in both
`opening_balance_vs_prior_year_closing.py` and
`suspense_account_scrutiny.py` is **not modified at all** — both already
take plain `TrialBalance`/`TallyData` objects and have no idea whether that
object came from a CSV file, an XML file, or a database row. Only each
check's *loading* wrapper changes:
- `run_check_from_files(prior_year_csv_path, current_year_csv_path, ...)`
  gains a sibling, `run_check_from_db(client_id, fy, version_id,
  tolerance=...)`, calling `db.queries.get_trial_balance` twice (once per
  scope) instead of `TrialBalance.from_csv` twice, then calling the
  unchanged `run_check`. Same `insufficient_data` handling on failure,
  translated from a `psycopg` error / empty-result case instead of
  `OSError`/`ValueError`.
- `run_check_from_file(tally_xml_path)` gains a sibling,
  `run_check_from_db(client_id, fy, version_id)`, calling
  `db.queries.get_tally_data` instead of `parse_tally_xml_data_file`, then
  calling the unchanged `run_check`.
- The existing file-based entry points (`run_check_from_files`,
  `run_check_from_file`) are **not removed** — `api.py`'s existing
  `/run-checks`/`/run-suspense-check` endpoints and the CLI `main()`
  functions keep working exactly as before; this only adds a new,
  additional way to run each check. Wiring `api.py` itself to call the new
  `_from_db` entry points is explicitly out of scope for this pass (see
  the user's own "before we touch the frontend/API layer" instruction) —
  it stays purely file-based for now.

### Verification (HARD RULE #4, re-applied for the new input source)
`db/load_sample_data.py` loads every `data-synthesizer` sample company
(both `samples/trial_balance/*` and `samples/tally_xml/*`) into
`scrutiny_engine`, then `tests/verify_against_data_synthesizer_via_db.py`
and `tests/verify_suspense_account_scrutiny_against_data_synthesizer_via_db.py`
re-run the exact same answer-key assertions as the original two
`verify_*_against_data_synthesizer.py` scripts, but call each check's new
`run_check_from_db` instead of loading files directly — proving the
Postgres-sourced path produces byte-for-byte (well, paisa-for-paisa)
identical results to the original file-sourced path, not just that it runs
without crashing.

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
  `unittest discover`. `verify_large_split_utf16_files.py` (added
  2026-08-06) is the same kind of standalone script, for
  `tally_xml_parser.py`'s encoding/split-file handling at real-world scale
  — see the `tally_xml_parser.py` bullet below.
- `trial_balance_csv_parser.py` — tolerant CSV → `TrialBalance` parser used
  by `api.py`'s `/parse-trial-balance`, independent of and more lenient than
  `schemas/trial_balance.py`'s strict `TrialBalance.from_csv` (which stays
  as the canonical, exact-format parser other checks can rely on). See its
  own docstring for exactly what variations are tolerated.
- `parse_error_classification.py` (added 2026-08-08) — turns a raw parse
  exception (from `tally_xml_parser.py` or `trial_balance_csv_parser.py`)
  into a plain-language, user-facing category + message + suggested fix,
  for `api.py`'s file-upload endpoints to return instead of a raw error
  string. Deliberately its own stdlib-only module, not inline in `api.py`,
  so it stays covered by the fast `unittest discover` suite even though
  `api.py` itself needs the venv (FastAPI) to even import — see "File-
  parsing error classification" under the `api.py` bullet below for the
  full category list and how each failure gets sorted into one.
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
  **Encoding + split-file support** (added 2026-08-06, found testing
  against real user Tally exports for the first time -- every sample used
  before this was `data-synthesizer`'s own UTF-8, single-combined-file
  output, which had been masking both gaps):
  - Real exports are commonly **UTF-16 with a byte-order mark**, not UTF-8.
    Every entry point takes raw bytes and sniffs the encoding itself via
    `_normalize_xml_encoding` (BOM-based: UTF-16 LE/BE or UTF-8; falls back
    to plain UTF-8 if no BOM, so every pre-existing sample still works
    unchanged) and also strips characters XML 1.0 doesn't allow at all
    before handing anything to `ElementTree` — both as a **numeric entity
    reference** (e.g. `&#4;`, `_strip_invalid_numeric_entities`) and,
    **found separately on 2026-08-07**, the same characters embedded
    **raw/literally** in the decoded text (`_strip_raw_illegal_control_chars`)
    — a real file had a bare ASCII `0x05` byte directly inside a
    `<STATKEY>` field (e.g. `"2023\x05376\x05Outward Invoice\x05S1.4.2023"`,
    apparently Tally's own internal delimiter joining several values into
    one field), which the entity-only fix didn't catch since it was never
    spelled out as `&#5;`. Both strip functions share the one definition of
    "valid" (`_is_valid_xml_char`) so they can't drift apart, and the raw
    version covers the *entire* XML 1.0 illegal range via a precomputed
    `str.translate` table, not just `0x05` — nothing guarantees that's the
    only raw control character Tally ever emits. `STATKEY` isn't read by
    any check today, so stripping is harmless there, but the strip runs
    over the WHOLE document (encoding normalization happens before any
    per-element parsing, so there's no way to scope it to one tag, and an
    illegal literal character anywhere breaks well-formedness for the whole
    file regardless of which field it's in) — a real risk in principle,
    since if this same delimiter pattern showed up inside a field this
    project actually reads, stripping would silently concatenate that
    field's joined sub-values with no separator, losing real information
    rather than discarding noise. **Confirmed clean against real client
    data (2026-08-07):** every occurrence of the raw control-character
    delimiter pattern in the real ~61MB `Transactions.xml` was checked, and
    it appears ONLY in `STATKEY` — `NARRATION`, `LEDGERNAME`, `PARTYNAME`,
    and `VOUCHERNUMBER` are all clean. Not a structural guarantee (a
    different real export could still differ), but confirmed against the
    one real file this was found in.
  - **A `<LEDGER>` with an empty/self-closing `<PARENT/>` is a legitimate
    "no parent group" state, not a missing-field error** (fixed 2026-08-09,
    a real client bug -- previously treated identically to the tag being
    entirely absent, both raising "missing required <PARENT> element").
    Confirmed real example: Tally's own reserved `"Profit & Loss A/c"`
    ledger genuinely has no parent group at all, and represents that with
    an empty `<PARENT/>`, not by omitting the tag. `_required_element_
    optional_text` (new) requires the ELEMENT to be present but allows
    empty text, returning `""` -- used for `PARENT` specifically; `PARENT`
    completely ABSENT (the element itself missing, not just empty) still
    raises, a judgment call based on every real file seen so far always
    emitting every known field's tag (populated or not) rather than ever
    omitting one outright -- same pattern already established for
    `<STATKEY>` above. An empty-string `parent` resolves correctly through
    `TallyData.resolve_top_level_group` (stops immediately, since `""` is
    never a key in `groups`) without falsely matching any of
    `PROFIT_AND_LOSS_PARENT_GROUPS`, so a reserved ledger like this stays
    correctly classified as a permanent/balance-sheet ledger, not excluded
    as P&L.
  - **A `<LEDGER>` with `<OPENINGBALANCE>` completely ABSENT is a legitimate
    zero opening balance, not a missing-field error** (fixed 2026-08-10, a
    related but distinct real client bug found right after the `<PARENT/>`
    fix above -- same `"Profit & Loss A/c"` ledger, but a different
    omission mechanism for a different field: Tally leaves `<PARENT/>`
    EMPTY-BUT-PRESENT, but OMITS `<OPENINGBALANCE>` ENTIRELY when it's
    zero, rather than writing `<OPENINGBALANCE>0.00</OPENINGBALANCE>`.
    **This directly contradicted the judgment call the `<PARENT/>` fix
    rested on** ("Tally always emits every known field's tag, populated or
    not, never omitting one outright") -- corrected in
    `_required_element_optional_text`'s docstring to note Tally's real
    behavior is field-specific, not one blanket rule. `_optional_decimal_
    element` (new) treats a completely absent element as a caller-supplied
    default (`Decimal("0.00")` for `OPENINGBALANCE`) rather than erroring;
    if the element IS present, it must still contain a valid, non-empty
    decimal -- only complete absence gets the default, not an empty or
    garbage value. Applied generally to every `<LEDGER>`, not special-cased
    by ledger name, since the same omission could plausibly appear on any
    ledger with a genuinely zero opening balance.
  - Real exports commonly arrive as **two separate files** -- one with only
    `<GROUP>`/`<LEDGER>` masters, another with only `<VOUCHER>` entries --
    rather than one combined file, and both can carry the same
    `<REPORTNAME>` regardless of actual content (never trusted for
    anything). `parse_tally_xml_fragment` parses one such file without
    requiring ledgers to be present or validating voucher-ledger references
    locally; `merge_tally_xml_fragments` combines multiple fragments and
    performs those checks once, correctly, against the complete merged
    picture; `parse_tally_xml_data_multi(files: List[bytes])` is the
    combined entry point. `parse_tally_xml_data` (single file) is
    unchanged/backward compatible -- still requires the file to be
    self-contained.
  Unit tests: `tests/test_tally_xml_parser.py` (70 tests: sign-convention
  math, P&L filtering, group-hierarchy resolution, encoding normalization
  — including both the entity-reference and raw-literal illegal-character
  cases — split-file merging, the `TallyXmlParseError` subclass hierarchy
  (`ErrorSubclassTests`), the empty-`<PARENT/>` fix
  (`EmptyParentLedgerTests`), the absent-`<OPENINGBALANCE>` fix
  (`AbsentOpeningBalanceTests`), every rejection path, `TallyData`
  field/method coverage) plus
  `tests/test_parse_error_classification.py` (10 tests) for
  `parse_error_classification.py`. Real-world-scale regression check
  (standalone, not part of `unittest discover` -- see below):
  `tests/verify_large_split_utf16_files.py`
  generates a ~9MB UTF-16 masters file + ~61MB UTF-16 transactions file on
  the fly (matching a real reported case exactly) with GST-specific noise
  tags and invalid numeric entities scattered throughout, and asserts both
  correctness (exact ledger/voucher counts, spot-checked balances, no
  entity artifacts leaking into parsed text) and that parsing+merging
  finishes in about a second, not minutes -- run with `python3
  tests/verify_large_split_utf16_files.py` (`--quick` for a much smaller,
  faster version during iteration). Also manually verified via a real HTTP
  upload to a running `api.py` server (`/parse-tally-xml-multi`, both files
  at once, ~70MB total) -- 200 OK in under 2 seconds; see "no request size
  limit" note on that endpoint below.
- A check module that imports sibling packages (i.e. any check built after
  the schemas/registry/coordinator layer landed) must be run as
  `python3 -m checks.<module_name> ...`, not as a bare script path — see
  the "CLI usage" note at the top of
  `checks/opening_balance_vs_prior_year_closing.py` for why.
- `api.py` — minimal FastAPI service exposing checks over HTTP (see
  "Conventions" exception above). Five endpoints so far:
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
  - `POST /parse-tally-xml` (added 2026-08-02) — raw Tally XML file upload
    (multipart/form-data, field name `file`) → the full structured
    `TallyData` shape: `{"ledgers": [{"name", "parent", "opening_balance"}],
    "vouchers": [{"vch_type", "voucher_number", "date", "narration",
    "legs": [{"ledger_name", "is_debit", "amount"}]}]}` (amounts as strings,
    same Decimal-precision reasoning as `/parse-trial-balance`). Parsing
    logic lives in `tally_xml_parser.py`'s `parse_tally_xml_data` (see
    "Structure" above) — 422 with a classified, plain-language error on
    malformed input (see "File-parsing error classification" below; not the
    parser's raw exception message string). Not yet wired to a
    check endpoint the way `/parse-trial-balance` feeds `/run-checks` --
    `checks/suspense_account_scrutiny.py` (the check actually validated
    against this data) has no HTTP endpoint yet, only a CLI entry point.
    Manually verified (2026-08-02) via curl against all 5 real
    data-synthesizer Tally XML samples: correct ledger/voucher counts for
    each, and the 4-error company's known phantom voucher (`JV-0012` on
    "Axis Bank CC A/c") round-trips through the endpoint with the exact
    same amount as `answer_key.json`. A non-XML file correctly returns 422.
    Expects ONE self-contained file (masters + vouchers, or masters alone)
    — see `/parse-tally-xml-multi` below for a real-world export split
    across separate files.
  - `POST /parse-tally-xml-multi` (added 2026-08-06) — same response shape
    as `/parse-tally-xml`, but takes MULTIPLE files (multipart/form-data,
    repeated field name `files`) and merges them via
    `tally_xml_parser.py`'s `parse_tally_xml_data_multi` — for a real-world
    export split across a masters-only file and a transactions-only file
    (see the `tally_xml_parser.py` bullet above). Which file is which is
    never assumed from filename or `<REPORTNAME>` — content alone decides,
    and file order doesn't matter. **No request size limit was hit or
    added** — checked this Starlette version's multipart parser source
    (`max_part_size`/`spool_max_size`, both 1MB) and confirmed by reading it
    that the 1MB cap applies only to plain form *fields*, not file uploads
    (files stream through a `SpooledTemporaryFile` with no size cap at all);
    confirmed empirically too, with a real HTTP POST of two real-world-scale
    UTF-16 files (~9MB + ~61MB, ~70MB total request) to a running server —
    200 OK in under 2 seconds, not rejected.
  - **File-parsing error classification** (added 2026-08-08, prompted by a
    real user seeing a raw error like `"Not well-formed XML: no element
    found: line 563384, column 7"` when a real Transactions.xml turned out
    to be cut off mid-transfer -- meaningless to a non-technical user).
    Every failure from `/parse-trial-balance`, `/parse-tally-xml`, and
    `/parse-tally-xml-multi` now returns a structured JSON `detail` instead
    of a raw exception string: `{"category", "is_file_problem", "message",
    "suggested_fix", "technical_detail"}`. Classification logic lives in the
    new `parse_error_classification.py` (stdlib-only, deliberately NOT
    inside `api.py` -- see that module's docstring for why: it needs to stay
    testable via the fast `unittest discover` suite, which can't import
    `api.py` itself since that requires FastAPI, this project's one
    dependency exception). `tally_xml_parser.py`'s `TallyXmlParseError` grew
    a small subclass hierarchy (`TallyXmlTruncatedError`,
    `TallyXmlEncodingError`, `TallyXmlMalformedError`,
    `TallyXmlNotATallyExportError`, plus the base class itself for anything
    else) so classification is a plain `isinstance` check, not string-
    matching error messages. Categories: `file_truncated` (the file's XML
    structure never reaches a complete state -- detected via expat's own
    error CODE, not message text; broadened empirically past just the one
    reported code, "no element found" -- truncating a realistic document at
    200 random points showed "unclosed token" firing roughly 3x as often,
    so both plus `unclosed CDATA section`/`partial character` for
    completeness are all treated as truncation), `unsupported_encoding`
    (can't decode as UTF-8/UTF-16 at all), `not_a_tally_export` (well-formed
    XML but wrong root element or no `<LEDGER>` masters anywhere),
    `not_valid_xml` (malformed XML for any other reason), `file_data_issue`
    (the base `TallyXmlParseError`/`TrialBalanceParseError` case -- a
    recognized problem with the file's DATA that doesn't need its own named
    category, e.g. a duplicate ledger or unbalanced voucher; the existing
    exception message is reused as both `message` and `technical_detail`
    since it's already reasonably clear), and `unknown` (a genuine
    catch-all for an exception that ISN'T one of this project's own
    recognized parse-error types at all -- `is_file_problem: False` and
    HTTP 500, not 422, since this means a bug in this project's own code,
    not a problem with the user's file). Every category except `unknown`
    returns 422. Unit tests: `tests/test_parse_error_classification.py` (10
    tests covering every category + the response-shape contract) and
    `tests/test_tally_xml_parser.py`'s `ErrorSubclassTests` (8 tests
    confirming the right SPECIFIC subclass gets raised, not just the base
    class). Verified against a real-shaped reproduction (not just unit
    tests): generated a ~25MB synthetic Transactions.xml, cut it off ~65%
    through right after a `</TALLYMESSAGE>` boundary (reproducing the exact
    real error signature -- "no element found" at a deep line number, not
    at the start), uploaded it to a running server via
    `/parse-tally-xml-multi` and got back the friendly `file_truncated`
    JSON response; uploaded the SAME file un-truncated and got a normal 200
    OK with the correct ledger/voucher counts, confirming no regression for
    valid files. Also spot-checked `not_valid_xml`, `not_a_tally_export`,
    and `unsupported_encoding` live against the running server.
  - `POST /run-suspense-check` (added 2026-08-02) — wraps
    `checks/suspense_account_scrutiny.py` specifically (same one-check-per-
    endpoint approach as `/run-checks`, not a registry-driven dispatcher).
    Request body is exactly the `TallyData` shape `/parse-tally-xml`
    returns (`{"ledgers": [...], "vouchers": [...]}`, amounts as strings),
    so the two chain directly: upload Tally XML to `/parse-tally-xml`, feed
    its response straight into `/run-suspense-check`. Returns the standard
    `CheckResult.to_dict()` array. `tests/test_api_manual_suspense.py` is
    the full-stack harness (same pattern as `test_api_manual.py`): uploads
    each real data-synthesizer Tally XML sample's raw file through both
    endpoints in sequence and confirms the flagged results match that
    sample's `answer_key.json` exactly — verified passing across all 5
    companies, the same result
    `verify_suspense_account_scrutiny_against_data_synthesizer.py` already
    proved at the Python-function level, now confirmed reachable over HTTP
    too.
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

## Why check #1 and Suspense Account Scrutiny are separate checks (not a workaround)
This came up twice while building the Tally XML pipeline (2026-08-02) and is
settled -- do not revisit this as "we should make check #1 work on Tally
XML" without re-reading this section first.

**The two checks test two different fields, and `data-synthesizer`'s Tally
XML generator deliberately keeps those two fields decoupled:**
- `opening_balance_vs_prior_year_closing.py` (check #1) tests: does
  `LEDGER.OPENINGBALANCE` (a single stated field) match the prior year's
  audited closing balance? A same-point-in-time continuity fact.
- `suspense_account_scrutiny.py` tests: does any voucher for the year post
  through a designated Suspense ledger? A voucher-trail / transaction-level
  fact.

**Attempt 1 (rejected): feed check #1 the voucher-summed *closing* balance**
(`opening_balance = opening + every voucher leg for the year`, i.e. what
`parse_tally_xml`/`TallyData.closing_balance()` computes). Fails because a
full year of realistic, uncorrelated trading vouchers legitimately moves an
active ledger's balance away from its opening figure -- that's normal
business activity, not a discrepancy. Empirically: 100% of eligible ledgers
were false-flagged in the two *clean* (zero-injected-error) sample
companies. See `tally_xml_parser.py`'s "KNOWN LIMITATION" docstring section
and the "Structure" entry above.

**Attempt 2 (rejected): feed check #1 the raw, unsummed `OPENINGBALANCE`
field directly** (no voucher summing at all -- exactly what check #1's CSV
path already does, just sourced from XML instead of a CSV cell). This
seemed like it should work, since `OPENINGBALANCE` is always set correctly
to the prior year's closing figure. It doesn't, and for a structural reason,
not a bug: `data-synthesizer`'s error injection **never touches
`OPENINGBALANCE`** -- every injected error is an extra phantom Journal
voucher instead (see that project's `generators/tally_xml/generate_tally_xml.py`,
"Error injection design"). So `OPENINGBALANCE` matches the prior year's
closing balance *exactly, in every single sample company this generator can
produce, whether or not an error was injected* -- it's true by construction,
not something that varies with the data. Fed into check #1 this way, the
result was 0 flagged ledgers in all 5 companies, including all 3
error-injected ones -- every one of the 9 injected errors silently missed.
Confirmed empirically (2026-08-02): the two clean companies correctly
showed 0 flagged (31 and 32 ledgers, all pass), but so did the three error
companies that should have shown 2, 3, and 4 flagged respectively.

**The conclusion:** this isn't "check #1 needs more work to support Tally
XML" -- it's that `opening_balance_vs_prior_year_closing.py` and
`suspense_account_scrutiny.py` are two independently valid, differently-scoped
checks, and this specific test-data generator only varies the field the
second one looks at. Check #1 remains correct and useful for its own
purpose (CSV-stated opening balances, or any future Tally XML error-injection
scheme that actually tampers with `OPENINGBALANCE` -- were one to exist);
Suspense Account Scrutiny is what actually exercises this generator's Tally
XML voucher data. Neither is a workaround for the other; do not merge them
or try to make one supersede the other.

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
  Gained a `run_check_from_db(client_id, fy, version_id, tolerance=...)`
  entry point (2026-08-06, `feature/postgres-data-layer`) sourcing both
  `TrialBalance` documents from Postgres via `db/queries.get_trial_balance`
  instead of CSV files — `run_check()` itself untouched. Re-validated via
  `tests/verify_against_data_synthesizer_via_db.py` against the same 5
  sample companies (loaded into Postgres by `db/load_sample_data.py`):
  identical PASS result to the file-sourced harness, same amounts to the
  paisa. `run_check_from_files` is unchanged and still the entry point
  `api.py`'s `/run-checks` and the CLI `main()` use — see CLAUDE.md's
  "Postgres data layer" section for why `api.py` wasn't touched in this
  pass.
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
  fixtures). Gained a `run_check_from_db(client_id, fy, version_id)` entry
  point (2026-08-06, `feature/postgres-data-layer`) sourcing `TallyData`
  from Postgres via `db/queries.get_tally_data` instead of parsing
  `tally_export.xml` — `run_check()` itself untouched. Re-validated via
  `tests/verify_suspense_account_scrutiny_against_data_synthesizer_via_db.py`
  against the same 5 sample companies: identical PASS result to the
  file-sourced harness. `run_check_from_file` is unchanged and still what
  `api.py`'s `/run-suspense-check` and the CLI `main()` use.
