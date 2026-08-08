"""Data access layer -- the ONLY module allowed to issue SQL against the
Postgres tables in db/schema.sql. Every check migrated onto this layer
(see checks/opening_balance_vs_prior_year_closing.py's run_check_from_db,
checks/suspense_account_scrutiny.py's run_check_from_db) and the sample
data loader (db/load_sample_data.py) call these functions instead of
writing their own queries -- see CLAUDE.md's "Postgres data layer" section,
"Data access layer (db/)" subsection for the full rationale.

Read functions return the exact same dataclasses schemas/ already defines
(TrialBalance, TallyLedgerMaster, TallyVoucher, TallyData) -- a caller can't
tell whether the object came from a file or from Postgres. Write functions
(insert_*) are upserts (ON CONFLICT DO UPDATE, matching db/schema.sql's
unique indexes/constraints) so the loader script is safe to re-run.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from db.connection import client_scoped_connection
from schemas.enums import DocumentScope
from schemas.tally_data import TallyData, TallyGroupMaster, TallyLedgerMaster, TallyVoucher, TallyVoucherLeg
from schemas.trial_balance import LedgerBalance, TrialBalance

# ---------------------------------------------------------------------
# Reads -- one function per document type, per the task's own naming.
# ---------------------------------------------------------------------


def get_trial_balance(
    client_id: str,
    fy: str,
    scope: DocumentScope,
    version_id: Optional[str] = None,
) -> TrialBalance:
    """Loads a TrialBalance for one (client, fy, scope[, version]).
    scope=VERSION_SCOPED requires version_id; scope=PERIOD_SCOPED_PRIOR_YEAR
    ignores it (that scope has no version_id column value -- see
    db/schema.sql's CHECK constraint). Returns an empty TrialBalance
    (ledgers=[]) if nothing is stored yet -- same "no error, just empty"
    contract as reading an empty CSV would not have, so callers (the
    checks' own run_check_from_db) are responsible for deciding whether
    zero ledgers means insufficient_data, matching how run_check_from_files
    already treats a missing file as insufficient_data.
    """
    if scope == DocumentScope.VERSION_SCOPED:
        if not version_id:
            raise ValueError("version_id is required when scope is VERSION_SCOPED")
        query = (
            "SELECT ledger_name, ledger_group, debit, credit FROM trial_balance_ledgers "
            "WHERE client_id = %s AND fy = %s AND scope = %s AND version_id = %s "
            "ORDER BY ledger_name"
        )
        params = (client_id, fy, scope.value, version_id)
    else:
        query = (
            "SELECT ledger_name, ledger_group, debit, credit FROM trial_balance_ledgers "
            "WHERE client_id = %s AND fy = %s AND scope = %s "
            "ORDER BY ledger_name"
        )
        params = (client_id, fy, scope.value)

    with client_scoped_connection(client_id) as cur:
        cur.execute(query, params)
        rows = cur.fetchall()

    return TrialBalance(
        ledgers=[
            LedgerBalance(name=name, group=group, debit=debit, credit=credit)
            for name, group, debit, credit in rows
        ]
    )


def get_tally_ledgers(client_id: str, fy: str, version_id: str) -> Dict[str, TallyLedgerMaster]:
    """Loads every ledger master for one (client, fy, version), keyed by
    name -- matching TallyData.ledgers' own shape.
    """
    with client_scoped_connection(client_id) as cur:
        cur.execute(
            "SELECT ledger_name, parent, opening_balance FROM tally_ledgers "
            "WHERE client_id = %s AND fy = %s AND version_id = %s",
            (client_id, fy, version_id),
        )
        rows = cur.fetchall()

    return {
        name: TallyLedgerMaster(name=name, parent=parent, opening_balance=opening_balance)
        for name, parent, opening_balance in rows
    }


def get_tally_vouchers(client_id: str, fy: str, version_id: str) -> List[TallyVoucher]:
    """Loads every voucher (with its legs, in original leg_order) for one
    (client, fy, version), ordered by date then voucher number.
    """
    with client_scoped_connection(client_id) as cur:
        cur.execute(
            "SELECT id, voucher_number, vch_type, voucher_date, narration FROM tally_vouchers "
            "WHERE client_id = %s AND fy = %s AND version_id = %s "
            "ORDER BY voucher_date, voucher_number",
            (client_id, fy, version_id),
        )
        voucher_rows = cur.fetchall()

        voucher_ids = [row[0] for row in voucher_rows]
        legs_by_voucher_id: Dict[int, List[TallyVoucherLeg]] = {vid: [] for vid in voucher_ids}
        if voucher_ids:
            cur.execute(
                "SELECT voucher_id, ledger_name, is_debit, amount FROM tally_voucher_legs "
                "WHERE voucher_id = ANY(%s) ORDER BY voucher_id, leg_order",
                (voucher_ids,),
            )
            for voucher_id, ledger_name, is_debit, amount in cur.fetchall():
                legs_by_voucher_id[voucher_id].append(
                    TallyVoucherLeg(ledger_name=ledger_name, is_debit=is_debit, amount=amount)
                )

    return [
        TallyVoucher(
            vch_type=vch_type,
            voucher_number=voucher_number,
            date=voucher_date.isoformat(),
            narration=narration,
            legs=legs_by_voucher_id[voucher_id],
        )
        for voucher_id, voucher_number, vch_type, voucher_date, narration in voucher_rows
    ]


def get_tally_groups(client_id: str, fy: str, version_id: str) -> Dict[str, TallyGroupMaster]:
    """Loads every <GROUP> master for one (client, fy, version), keyed by
    name -- matching TallyData.groups' own shape. Usually a handful of rows
    (or none, for exports that never emit group masters).
    """
    with client_scoped_connection(client_id) as cur:
        cur.execute(
            "SELECT group_name, parent FROM tally_groups "
            "WHERE client_id = %s AND fy = %s AND version_id = %s",
            (client_id, fy, version_id),
        )
        rows = cur.fetchall()

    return {name: TallyGroupMaster(name=name, parent=parent) for name, parent in rows}


def get_tally_data(client_id: str, fy: str, version_id: str) -> TallyData:
    """Composes get_tally_ledgers + get_tally_vouchers + get_tally_groups
    into the full TallyData shape checks/suspense_account_scrutiny.py needs
    (and the frontend's Tally Data Visualizer reads back via
    /version-parsed-data). Doesn't issue SQL itself -- still only calls the
    functions above.
    """
    return TallyData(
        ledgers=get_tally_ledgers(client_id, fy, version_id),
        vouchers=get_tally_vouchers(client_id, fy, version_id),
        groups=get_tally_groups(client_id, fy, version_id),
    )


# ---------------------------------------------------------------------
# Writes -- upserts, used only by db/load_sample_data.py today. Kept in
# this module rather than the loader script itself so the loader also
# never issues SQL directly, same rule as the checks.
# ---------------------------------------------------------------------


def insert_trial_balance_ledgers(
    client_id: str,
    fy: str,
    scope: DocumentScope,
    trial_balance: TrialBalance,
    version_id: Optional[str] = None,
) -> None:
    if scope == DocumentScope.VERSION_SCOPED and not version_id:
        raise ValueError("version_id is required when scope is VERSION_SCOPED")
    if scope == DocumentScope.PERIOD_SCOPED_PRIOR_YEAR:
        version_id = None

    if scope == DocumentScope.VERSION_SCOPED:
        query = """
            INSERT INTO trial_balance_ledgers
                (client_id, fy, version_id, scope, ledger_name, ledger_group, debit, credit)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (client_id, fy, version_id, ledger_name) WHERE scope = 'version_scoped'
            DO UPDATE SET ledger_group = EXCLUDED.ledger_group,
                           debit = EXCLUDED.debit,
                           credit = EXCLUDED.credit
        """
    else:
        query = """
            INSERT INTO trial_balance_ledgers
                (client_id, fy, version_id, scope, ledger_name, ledger_group, debit, credit)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (client_id, fy, ledger_name) WHERE scope = 'period_scoped_prior_year'
            DO UPDATE SET ledger_group = EXCLUDED.ledger_group,
                           debit = EXCLUDED.debit,
                           credit = EXCLUDED.credit
        """

    with client_scoped_connection(client_id) as cur:
        for ledger in trial_balance.ledgers:
            cur.execute(
                query,
                (client_id, fy, version_id, scope.value, ledger.name, ledger.group, ledger.debit, ledger.credit),
            )


def insert_tally_data(client_id: str, fy: str, version_id: str, tally_data: TallyData) -> None:
    """Replaces a version's Tally data wholesale (DELETE-then-INSERT), rather
    than upserting row-by-row. Ledger + group masters ARE unique by name, so
    those stay ON CONFLICT upserts; vouchers are the reason for the wholesale
    replace: voucher_number is NOT unique within a real export (see
    db/schema.sql -- the anonymized real sample has 4623 vouchers over only
    2047 distinct numbers), so an upsert-by-number would silently collapse
    every duplicate-numbered voucher and lose over half the file. Deleting a
    version's vouchers first (legs cascade via the FK) and then plain-INSERTing
    all of them preserves every voucher, and is still safe to re-run: it all
    runs inside one client-scoped transaction (client_scoped_connection
    commits/rolls back as a unit), so a re-run can never leave a partial set.
    """
    with client_scoped_connection(client_id) as cur:
        for ledger in tally_data.ledgers.values():
            cur.execute(
                """
                INSERT INTO tally_ledgers (client_id, fy, version_id, ledger_name, parent, opening_balance)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (client_id, fy, version_id, ledger_name)
                DO UPDATE SET parent = EXCLUDED.parent, opening_balance = EXCLUDED.opening_balance
                """,
                (client_id, fy, version_id, ledger.name, ledger.parent, ledger.opening_balance),
            )

        for group in tally_data.groups.values():
            cur.execute(
                """
                INSERT INTO tally_groups (client_id, fy, version_id, group_name, parent)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (client_id, fy, version_id, group_name)
                DO UPDATE SET parent = EXCLUDED.parent
                """,
                (client_id, fy, version_id, group.name, group.parent),
            )

        # Wholesale replace of this version's vouchers (legs cascade on the FK)
        # -- see the docstring: voucher_number is not unique, so an upsert would
        # collapse duplicates and lose vouchers.
        cur.execute(
            "DELETE FROM tally_vouchers WHERE client_id = %s AND fy = %s AND version_id = %s",
            (client_id, fy, version_id),
        )
        for voucher in tally_data.vouchers:
            cur.execute(
                """
                INSERT INTO tally_vouchers
                    (client_id, fy, version_id, voucher_number, vch_type, voucher_date, narration)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (client_id, fy, version_id, voucher.voucher_number, voucher.vch_type, voucher.date, voucher.narration),
            )
            (voucher_id,) = cur.fetchone()

            for leg_order, leg in enumerate(voucher.legs):
                cur.execute(
                    """
                    INSERT INTO tally_voucher_legs
                        (voucher_id, client_id, fy, version_id, ledger_name, is_debit, amount, leg_order)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (voucher_id, client_id, fy, version_id, leg.ledger_name, leg.is_debit, leg.amount, leg_order),
                )


def delete_version_scoped_data(client_id: str, fy: str, version_id: str) -> Dict[str, int]:
    """Removes ALL version-scoped rows for one (client, fy, version) across
    every table -- tally_ledgers/tally_groups/tally_vouchers (legs cascade
    via the tally_voucher_legs FK's ON DELETE CASCADE) and the
    version_scoped trial_balance_ledgers rows. Used as a compensating
    cleanup: the frontend stores parsed data into Postgres BEFORE it uploads
    files to Storage / writes the Firestore version doc (so a committed
    version can never lack its queryable rows -- the visualizer and the
    checks both read them from here now), and calls this to roll those rows
    back if a later step in the same upload fails, so a failed upload never
    leaves orphaned Postgres data behind. Returns per-table deleted counts.
    Never touches period_scoped_prior_year trial-balance rows -- those aren't
    version-scoped and don't belong to any single version's upload.
    """
    with client_scoped_connection(client_id) as cur:
        cur.execute(
            "DELETE FROM tally_vouchers WHERE client_id = %s AND fy = %s AND version_id = %s",
            (client_id, fy, version_id),
        )
        deleted_vouchers = cur.rowcount
        cur.execute(
            "DELETE FROM tally_ledgers WHERE client_id = %s AND fy = %s AND version_id = %s",
            (client_id, fy, version_id),
        )
        deleted_ledgers = cur.rowcount
        cur.execute(
            "DELETE FROM tally_groups WHERE client_id = %s AND fy = %s AND version_id = %s",
            (client_id, fy, version_id),
        )
        deleted_groups = cur.rowcount
        cur.execute(
            "DELETE FROM trial_balance_ledgers "
            "WHERE client_id = %s AND fy = %s AND version_id = %s AND scope = 'version_scoped'",
            (client_id, fy, version_id),
        )
        deleted_trial_balance = cur.rowcount

    return {
        "tally_ledgers": deleted_ledgers,
        "tally_groups": deleted_groups,
        "tally_vouchers": deleted_vouchers,
        "trial_balance_ledgers": deleted_trial_balance,
    }
