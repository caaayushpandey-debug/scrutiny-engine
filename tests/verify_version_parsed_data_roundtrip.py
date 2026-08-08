"""Verifies the tally_groups persistence + version-scoped delete cleanup added
2026-08-08 for the frontend's "full parsed data lives in Postgres, not embedded
in Firestore" change (see CLAUDE.md's "Version parsed-data read-back + group
persistence" section and the frontend repo's feature/large-file-upload-fix).

Standalone (not part of `unittest discover`) because it needs the live local
scrutiny_engine Postgres -- same convention as verify_against_data_synthesizer_
via_db.py. Self-skips (exit 0) when the DB is unreachable, so it never breaks a
checkout without Postgres running.

Proves, against a throwaway client_id (cleaned up at the end no matter what):
1. insert_tally_data now persists <GROUP> masters, and get_tally_data reads
   them back into TallyData.groups (the field the visualizer's Balance Sheet /
   P&L hierarchy walk needs) -- ledgers + vouchers + legs still round-trip too.
2. A version-scoped TrialBalance stored for the same version is readable back.
3. delete_version_scoped_data removes ALL of it (tally_ledgers / tally_groups /
   tally_vouchers + cascaded legs / version_scoped trial_balance_ledgers) and
   reports accurate per-table counts, and leaves a period_scoped_prior_year
   trial-balance row for the same client/fy UNTOUCHED (it isn't version data).

Run:  ./venv/bin/python3 tests/verify_version_parsed_data_roundtrip.py
"""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg  # noqa: E402

from db.connection import client_scoped_connection  # noqa: E402
from db.queries import (  # noqa: E402
    delete_version_scoped_data,
    get_tally_data,
    get_trial_balance,
    insert_tally_data,
    insert_trial_balance_ledgers,
)
from schemas.enums import DocumentScope  # noqa: E402
from schemas.tally_data import (  # noqa: E402
    TallyData,
    TallyGroupMaster,
    TallyLedgerMaster,
    TallyVoucher,
    TallyVoucherLeg,
)
from schemas.trial_balance import LedgerBalance, TrialBalance  # noqa: E402

CLIENT_ID = "test_vpd_roundtrip_client"
FY = "2025-26"
VERSION_ID = "V1"

failures = []


def check(cond: bool, msg: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {msg}")
    if not cond:
        failures.append(msg)


def build_tally_data() -> TallyData:
    return TallyData(
        ledgers={
            "Overseas Debtors A/c": TallyLedgerMaster(
                name="Overseas Debtors A/c", parent="Overseas Debtors", opening_balance=Decimal("50000.00")
            ),
            "Sales Export": TallyLedgerMaster(
                name="Sales Export", parent="Sales Accounts", opening_balance=Decimal("0.00")
            ),
        },
        vouchers=[
            TallyVoucher(
                vch_type="Sales",
                voucher_number="INV-001",
                date="2025-04-10",
                narration="Export sale",
                legs=[
                    TallyVoucherLeg(ledger_name="Overseas Debtors A/c", is_debit=True, amount=Decimal("-11800.00")),
                    TallyVoucherLeg(ledger_name="Sales Export", is_debit=False, amount=Decimal("11800.00")),
                ],
            )
        ],
        # The crux of this test: a custom group nested under a built-in primary,
        # which is exactly what resolve_top_level_group needs to walk.
        groups={
            "Overseas Debtors": TallyGroupMaster(name="Overseas Debtors", parent="Sundry Debtors"),
            "Sundry Debtors": TallyGroupMaster(name="Sundry Debtors", parent="Current Assets"),
        },
    )


def cleanup() -> None:
    # Direct, unconditional teardown of everything this script could have
    # written for CLIENT_ID -- including the period_scoped_prior_year row that
    # delete_version_scoped_data deliberately leaves behind.
    try:
        with client_scoped_connection(CLIENT_ID) as cur:
            cur.execute("DELETE FROM tally_vouchers WHERE client_id = %s", (CLIENT_ID,))
            cur.execute("DELETE FROM tally_ledgers WHERE client_id = %s", (CLIENT_ID,))
            cur.execute("DELETE FROM tally_groups WHERE client_id = %s", (CLIENT_ID,))
            cur.execute("DELETE FROM trial_balance_ledgers WHERE client_id = %s", (CLIENT_ID,))
    except Exception as e:
        print(f"  (cleanup note: {e})")


def main() -> int:
    try:
        cleanup()  # start from a clean slate
    except psycopg.OperationalError:
        print("Postgres unreachable -- skipping (expected on a checkout without the DB running).")
        return 0
    except Exception as e:
        # Any other connection-time failure also means no usable DB here.
        print(f"Postgres not usable ({e}) -- skipping.")
        return 0

    try:
        # --- 1. Tally data round-trip, groups included ---
        print("=== insert_tally_data + get_tally_data (groups included) ===")
        insert_tally_data(CLIENT_ID, FY, VERSION_ID, build_tally_data())
        loaded = get_tally_data(CLIENT_ID, FY, VERSION_ID)
        check(len(loaded.ledgers) == 2, f"2 ledgers round-tripped (got {len(loaded.ledgers)})")
        check(len(loaded.vouchers) == 1, f"1 voucher round-tripped (got {len(loaded.vouchers)})")
        check(
            len(loaded.vouchers[0].legs) == 2,
            f"voucher has 2 legs (got {len(loaded.vouchers[0].legs) if loaded.vouchers else 0})",
        )
        check(len(loaded.groups) == 2, f"2 GROUP masters round-tripped (got {len(loaded.groups)})")
        check(
            loaded.groups.get("Overseas Debtors") is not None
            and loaded.groups["Overseas Debtors"].parent == "Sundry Debtors",
            "custom group 'Overseas Debtors' -> parent 'Sundry Debtors' preserved",
        )
        # The whole point of persisting groups: the hierarchy walk resolves a
        # custom-sub-grouped ledger up to its built-in primary.
        check(
            loaded.resolve_top_level_group("Overseas Debtors A/c") == "Current Assets",
            "resolve_top_level_group walks Overseas Debtors A/c -> Current Assets (needs groups)",
        )

        # --- 2. Version-scoped trial balance readable back ---
        print("\n=== version-scoped trial balance round-trip ===")
        insert_trial_balance_ledgers(
            CLIENT_ID,
            FY,
            DocumentScope.VERSION_SCOPED,
            TrialBalance(ledgers=[LedgerBalance(name="Cash", group="Cash-in-Hand", debit=Decimal("1000.00"), credit=Decimal("0.00"))]),
            version_id=VERSION_ID,
        )
        # Also store a prior-year row that delete_version_scoped_data must NOT touch.
        insert_trial_balance_ledgers(
            CLIENT_ID,
            FY,
            DocumentScope.PERIOD_SCOPED_PRIOR_YEAR,
            TrialBalance(ledgers=[LedgerBalance(name="Cash", group="Cash-in-Hand", debit=Decimal("900.00"), credit=Decimal("0.00"))]),
        )
        tb = get_trial_balance(CLIENT_ID, FY, DocumentScope.VERSION_SCOPED, VERSION_ID)
        check(len(tb.ledgers) == 1, f"version-scoped trial balance has 1 ledger (got {len(tb.ledgers)})")

        # --- 3. Compensating delete removes version data only ---
        print("\n=== delete_version_scoped_data (compensating cleanup) ===")
        deleted = delete_version_scoped_data(CLIENT_ID, FY, VERSION_ID)
        check(deleted["tally_ledgers"] == 2, f"deleted 2 tally_ledgers (got {deleted['tally_ledgers']})")
        check(deleted["tally_groups"] == 2, f"deleted 2 tally_groups (got {deleted['tally_groups']})")
        check(deleted["tally_vouchers"] == 1, f"deleted 1 tally_voucher (got {deleted['tally_vouchers']})")
        check(
            deleted["trial_balance_ledgers"] == 1,
            f"deleted 1 version_scoped trial_balance row (got {deleted['trial_balance_ledgers']})",
        )
        after = get_tally_data(CLIENT_ID, FY, VERSION_ID)
        check(
            not after.ledgers and not after.vouchers and not after.groups,
            "get_tally_data empty after delete (ledgers/vouchers/groups all gone)",
        )
        prior = get_trial_balance(CLIENT_ID, FY, DocumentScope.PERIOD_SCOPED_PRIOR_YEAR)
        check(len(prior.ledgers) == 1, "period_scoped_prior_year trial balance left UNTOUCHED by version delete")
    finally:
        cleanup()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s) failed")
        return 1
    print("All version parsed-data round-trip checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
