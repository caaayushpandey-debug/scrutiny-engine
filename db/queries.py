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
from schemas.tally_data import TallyData, TallyLedgerMaster, TallyVoucher, TallyVoucherLeg
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


def get_tally_data(client_id: str, fy: str, version_id: str) -> TallyData:
    """Composes get_tally_ledgers + get_tally_vouchers into the full
    TallyData shape checks/suspense_account_scrutiny.py needs. Doesn't
    issue SQL itself -- still only calls the two functions above.
    """
    return TallyData(
        ledgers=get_tally_ledgers(client_id, fy, version_id),
        vouchers=get_tally_vouchers(client_id, fy, version_id),
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
    """Upserts every ledger master, voucher, and voucher leg in tally_data.
    Legs have no natural unique key of their own (see CLAUDE.md), so a
    voucher's legs are replaced wholesale (DELETE then re-INSERT) rather
    than upserted row-by-row -- safe because this always runs inside one
    client-scoped transaction (client_scoped_connection commits/rolls back
    as a unit), so a re-run can never leave a voucher with a partial leg set.
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

        for voucher in tally_data.vouchers:
            cur.execute(
                """
                INSERT INTO tally_vouchers
                    (client_id, fy, version_id, voucher_number, vch_type, voucher_date, narration)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (client_id, fy, version_id, voucher_number)
                DO UPDATE SET vch_type = EXCLUDED.vch_type,
                               voucher_date = EXCLUDED.voucher_date,
                               narration = EXCLUDED.narration
                RETURNING id
                """,
                (client_id, fy, version_id, voucher.voucher_number, voucher.vch_type, voucher.date, voucher.narration),
            )
            (voucher_id,) = cur.fetchone()

            cur.execute("DELETE FROM tally_voucher_legs WHERE voucher_id = %s", (voucher_id,))
            for leg_order, leg in enumerate(voucher.legs):
                cur.execute(
                    """
                    INSERT INTO tally_voucher_legs
                        (voucher_id, client_id, fy, version_id, ledger_name, is_debit, amount, leg_order)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (voucher_id, client_id, fy, version_id, leg.ledger_name, leg.is_debit, leg.amount, leg_order),
                )
