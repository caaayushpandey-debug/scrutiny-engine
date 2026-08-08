-- Postgres schema for the scrutiny-engine data layer.
-- See CLAUDE.md's "Postgres data layer" section for the full design
-- rationale -- this file is the DDL that section describes, nothing more.
--
-- Run as the database owner/admin role (NOT scrutiny_app):
--   psql -d scrutiny_engine -f db/schema.sql
--
-- Safe to re-run: tables use CREATE TABLE IF NOT EXISTS, indexes use
-- CREATE INDEX IF NOT EXISTS / CREATE UNIQUE INDEX IF NOT EXISTS, and the
-- scrutiny_app role creation is guarded against "already exists".

-- ---------------------------------------------------------------------
-- Application role. RLS policies below apply unconditionally to this role
-- (non-superuser, does not own these tables) -- see CLAUDE.md's
-- "Row-Level Security" subsection for why that matters (RLS is otherwise
-- bypassed for table owners/superusers unless FORCE ROW LEVEL SECURITY is
-- also set).
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'scrutiny_app') THEN
        CREATE ROLE scrutiny_app LOGIN;
    END IF;
END
$$;

GRANT CONNECT ON DATABASE scrutiny_engine TO scrutiny_app;
GRANT USAGE ON SCHEMA public TO scrutiny_app;

-- ---------------------------------------------------------------------
-- trial_balance_ledgers -- schemas/trial_balance.py's LedgerBalance.
-- TrialBalance is the one document type with two scopes (see
-- schemas/enums.py's DEFAULT_SCOPE_BY_DOCUMENT_TYPE docstring), so this
-- table carries an explicit `scope` column and version_id is nullable --
-- NULL exactly when scope = 'period_scoped_prior_year' (enforced below).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trial_balance_ledgers (
    id              BIGSERIAL PRIMARY KEY,
    client_id       TEXT NOT NULL,
    fy              TEXT NOT NULL,
    version_id      TEXT,
    scope           TEXT NOT NULL CHECK (scope IN ('version_scoped', 'period_scoped_prior_year')),
    ledger_name     TEXT NOT NULL,
    ledger_group    TEXT NOT NULL,
    debit           NUMERIC(18, 2) NOT NULL,
    credit          NUMERIC(18, 2) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT trial_balance_ledgers_version_id_matches_scope CHECK (
        (scope = 'version_scoped' AND version_id IS NOT NULL) OR
        (scope = 'period_scoped_prior_year' AND version_id IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_trial_balance_ledgers_client_fy
    ON trial_balance_ledgers (client_id, fy);

-- Two partial unique indexes, not one plain UNIQUE(..., version_id, ...) --
-- see CLAUDE.md's "Indexes" subsection for why a single constraint
-- including a nullable version_id would silently allow duplicate
-- prior-year rows (Postgres treats NULL <> NULL in unique indexes).
CREATE UNIQUE INDEX IF NOT EXISTS uq_trial_balance_ledgers_version_scoped
    ON trial_balance_ledgers (client_id, fy, version_id, ledger_name)
    WHERE scope = 'version_scoped';

CREATE UNIQUE INDEX IF NOT EXISTS uq_trial_balance_ledgers_prior_year
    ON trial_balance_ledgers (client_id, fy, ledger_name)
    WHERE scope = 'period_scoped_prior_year';

ALTER TABLE trial_balance_ledgers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS client_isolation ON trial_balance_ledgers;
CREATE POLICY client_isolation ON trial_balance_ledgers
    USING (client_id = current_setting('app.current_client_id', true));

GRANT SELECT, INSERT, UPDATE, DELETE ON trial_balance_ledgers TO scrutiny_app;
GRANT USAGE, SELECT ON SEQUENCE trial_balance_ledgers_id_seq TO scrutiny_app;

-- ---------------------------------------------------------------------
-- tally_ledgers -- schemas/tally_data.py's TallyLedgerMaster.
-- TALLY_DATA has no prior-year role (always VERSION_SCOPED, see
-- schemas/enums.py), so version_id is always NOT NULL and no scope column
-- is needed.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tally_ledgers (
    id                  BIGSERIAL PRIMARY KEY,
    client_id           TEXT NOT NULL,
    fy                  TEXT NOT NULL,
    version_id          TEXT NOT NULL,
    ledger_name         TEXT NOT NULL,
    parent              TEXT NOT NULL,
    opening_balance     NUMERIC(18, 2) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tally_ledgers_client_fy
    ON tally_ledgers (client_id, fy);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tally_ledgers
    ON tally_ledgers (client_id, fy, version_id, ledger_name);

ALTER TABLE tally_ledgers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS client_isolation ON tally_ledgers;
CREATE POLICY client_isolation ON tally_ledgers
    USING (client_id = current_setting('app.current_client_id', true));

GRANT SELECT, INSERT, UPDATE, DELETE ON tally_ledgers TO scrutiny_app;
GRANT USAGE, SELECT ON SEQUENCE tally_ledgers_id_seq TO scrutiny_app;

-- ---------------------------------------------------------------------
-- tally_vouchers -- schemas/tally_data.py's TallyVoucher, minus legs
-- (its own table below).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tally_vouchers (
    id                  BIGSERIAL PRIMARY KEY,
    client_id           TEXT NOT NULL,
    fy                  TEXT NOT NULL,
    version_id          TEXT NOT NULL,
    voucher_number      TEXT NOT NULL,
    vch_type            TEXT NOT NULL,
    voucher_date        DATE NOT NULL,
    narration           TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tally_vouchers_client_fy
    ON tally_vouchers (client_id, fy);

-- voucher_number is deliberately NOT unique within a version. Real Tally
-- exports routinely reuse a voucher number across entries (and leave many
-- blank) -- the anonymized real sample carries 4623 vouchers spanning only
-- 2047 distinct numbers (one number appears 6 times; 81 have no number at
-- all). An earlier UNIQUE(client_id, fy, version_id, voucher_number) index
-- silently collapsed those ~2576 duplicate-numbered vouchers on insert,
-- losing over half the file's real vouchers (and their legs). This is a plain
-- (non-unique) index for lookup performance only; insert_tally_data replaces
-- a version's vouchers wholesale (DELETE-then-INSERT, see db/queries.py)
-- rather than upserting by number, so every voucher is preserved. Callers
-- that reference a voucher by number (suspense_account_scrutiny.py's
-- SourceReference) tolerate a repeated number the same way the source file
-- does.
-- Migration: drop the old UNIQUE index if a pre-2026-08-08 schema created it,
-- so re-applying this file converges an existing database to the corrected
-- (non-unique) shape rather than leaving the collapsing constraint in place.
DROP INDEX IF EXISTS uq_tally_vouchers;
CREATE INDEX IF NOT EXISTS idx_tally_vouchers_version
    ON tally_vouchers (client_id, fy, version_id);

ALTER TABLE tally_vouchers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS client_isolation ON tally_vouchers;
CREATE POLICY client_isolation ON tally_vouchers
    USING (client_id = current_setting('app.current_client_id', true));

GRANT SELECT, INSERT, UPDATE, DELETE ON tally_vouchers TO scrutiny_app;
GRANT USAGE, SELECT ON SEQUENCE tally_vouchers_id_seq TO scrutiny_app;

-- ---------------------------------------------------------------------
-- tally_voucher_legs -- schemas/tally_data.py's TallyVoucherLeg.
-- client_id/fy/version_id are denormalized here (not just voucher_id FK)
-- specifically so the RLS policy can filter directly on this table without
-- a join -- see CLAUDE.md's "Tables" subsection.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tally_voucher_legs (
    id              BIGSERIAL PRIMARY KEY,
    voucher_id      BIGINT NOT NULL REFERENCES tally_vouchers(id) ON DELETE CASCADE,
    client_id       TEXT NOT NULL,
    fy              TEXT NOT NULL,
    version_id      TEXT NOT NULL,
    ledger_name     TEXT NOT NULL,
    is_debit        BOOLEAN NOT NULL,
    amount          NUMERIC(18, 2) NOT NULL,
    leg_order       INTEGER NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tally_voucher_legs_voucher_id
    ON tally_voucher_legs (voucher_id);

CREATE INDEX IF NOT EXISTS idx_tally_voucher_legs_client_fy
    ON tally_voucher_legs (client_id, fy);

ALTER TABLE tally_voucher_legs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS client_isolation ON tally_voucher_legs;
CREATE POLICY client_isolation ON tally_voucher_legs
    USING (client_id = current_setting('app.current_client_id', true));

GRANT SELECT, INSERT, UPDATE, DELETE ON tally_voucher_legs TO scrutiny_app;
GRANT USAGE, SELECT ON SEQUENCE tally_voucher_legs_id_seq TO scrutiny_app;

-- ---------------------------------------------------------------------
-- tally_groups -- schemas/tally_data.py's TallyGroupMaster (a <GROUP>
-- master from a Tally XML export). Added 2026-08-08: originally TallyData's
-- groups were only ever kept in the frontend's embedded Firestore copy and
-- dropped at the Postgres store step. Once the frontend moved the visualizer
-- to read the full parsed dataset back FROM Postgres (rather than embedding
-- a ~1MB copy per version doc), groups had to be persisted here too, or the
-- visualizer's Balance Sheet / P&L classification would lose the group
-- hierarchy walk (resolve_top_level_group) for exports that emit <GROUP>
-- masters -- real exports routinely do (the anonymized real files carry 44).
-- Like tally_ledgers, TALLY_DATA is always VERSION_SCOPED, so version_id is
-- NOT NULL and there is no scope column.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tally_groups (
    id                  BIGSERIAL PRIMARY KEY,
    client_id           TEXT NOT NULL,
    fy                  TEXT NOT NULL,
    version_id          TEXT NOT NULL,
    group_name          TEXT NOT NULL,
    parent              TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tally_groups_client_fy
    ON tally_groups (client_id, fy);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tally_groups
    ON tally_groups (client_id, fy, version_id, group_name);

ALTER TABLE tally_groups ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS client_isolation ON tally_groups;
CREATE POLICY client_isolation ON tally_groups
    USING (client_id = current_setting('app.current_client_id', true));

GRANT SELECT, INSERT, UPDATE, DELETE ON tally_groups TO scrutiny_app;
GRANT USAGE, SELECT ON SEQUENCE tally_groups_id_seq TO scrutiny_app;
