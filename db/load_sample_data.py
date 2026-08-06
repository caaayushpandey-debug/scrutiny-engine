"""One-time (re-runnable) loader: populates the local scrutiny_engine
Postgres database from the sibling data-synthesizer repo's existing sample
output, so the Postgres-backed checks (run_check_from_db, see
checks/opening_balance_vs_prior_year_closing.py and
checks/suspense_account_scrutiny.py) have real, answer-key-backed data to
run HARD RULE #4 verification against -- see CLAUDE.md's "Postgres data
layer" > "Local setup" and "Verification" subsections.

There is no real client/FY/version registry in this project (that lives in
scrutiny-engine-frontend's Firestore, which this loader does not read from
or write to) -- client_id/fy/version_id are assigned here, deterministically,
from each sample company's directory name and its own answer_key.json:

- Every trial_balance sample company (samples/trial_balance/<dir>/) becomes
  client_id = "tb_<dir>", loaded as TWO TrialBalance rows-sets sharing the
  SAME fy (the current_year_fy from that company's answer_key.json -- see
  "Document scope model" in CLAUDE.md: the fy a check runs against is always
  the year under audit, with the prior year's closing balance as
  supplementary context for that same fy, not a separate fy of its own):
    - prior_year_closing_trial_balance.csv -> scope=PERIOD_SCOPED_PRIOR_YEAR,
      version_id=NULL.
    - current_year_opening_trial_balance.csv -> scope=VERSION_SCOPED,
      version_id="v1" (data-synthesizer only ever produces one version per
      company; there is no V2/V3 sample data to load).
- Every tally_xml sample company (samples/tally_xml/<dir>/) becomes
  client_id = "txml_<dir>" (a DIFFERENT prefix, not reusing "tb_" or the
  bare directory name -- trial_balance/03_... and tally_xml/03_... are two
  different real companies that happen to share an index number in
  data-synthesizer's own sample set, not the same client under two
  document types; conflating them would silently merge unrelated
  companies' data under one client_id), fy = current_year_fy from that
  company's answer_key.json, version_id = "v1" (same reasoning as above).

Usage:
    ./venv/bin/python3 -m db.load_sample_data [data_synthesizer_root]

Defaults to ../data-synthesizer relative to this project (sibling repo),
same convention as tests/verify_against_data_synthesizer.py's own default
samples-root resolution. Safe to re-run -- every insert in db/queries.py is
an upsert.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.queries import insert_tally_data, insert_trial_balance_ledgers
from schemas.enums import DocumentScope
from schemas.trial_balance import TrialBalance
from tally_xml_parser import parse_tally_xml_data_file

VERSION_ID = "v1"


def load_trial_balance_company(company_dir: Path) -> str:
    answer_key_path = company_dir / "answer_key.json"
    prior_path = company_dir / "prior_year_closing_trial_balance.csv"
    current_path = company_dir / "current_year_opening_trial_balance.csv"

    with open(answer_key_path) as f:
        answer_key = json.load(f)
    fy = answer_key["current_year_fy"]

    client_id = f"tb_{company_dir.name}"

    prior_tb = TrialBalance.from_csv(str(prior_path))
    insert_trial_balance_ledgers(client_id, fy, DocumentScope.PERIOD_SCOPED_PRIOR_YEAR, prior_tb)

    current_tb = TrialBalance.from_csv(str(current_path))
    insert_trial_balance_ledgers(client_id, fy, DocumentScope.VERSION_SCOPED, current_tb, version_id=VERSION_ID)

    return (
        f"{client_id}: fy={fy} version={VERSION_ID} -- "
        f"{len(prior_tb.ledgers)} prior-year ledgers, {len(current_tb.ledgers)} current-year ledgers"
    )


def load_tally_xml_company(company_dir: Path) -> str:
    answer_key_path = company_dir / "answer_key.json"
    xml_path = company_dir / "tally_export.xml"

    with open(answer_key_path) as f:
        answer_key = json.load(f)
    fy = answer_key["current_year_fy"]

    client_id = f"txml_{company_dir.name}"

    tally_data = parse_tally_xml_data_file(str(xml_path))
    insert_tally_data(client_id, fy, VERSION_ID, tally_data)

    return (
        f"{client_id}: fy={fy} version={VERSION_ID} -- "
        f"{len(tally_data.ledgers)} ledgers, {len(tally_data.vouchers)} vouchers"
    )


def main():
    default_root = Path(__file__).resolve().parent.parent.parent / "data-synthesizer"
    ds_root = Path(sys.argv[1]) if len(sys.argv) > 1 else default_root

    tb_root = ds_root / "samples" / "trial_balance"
    xml_root = ds_root / "samples" / "tally_xml"

    if not tb_root.exists() or not xml_root.exists():
        print(f"Expected sample directories not found under {ds_root}")
        print("Pass the data-synthesizer repo root as an argument, e.g.:")
        print("  ./venv/bin/python3 -m db.load_sample_data /path/to/data-synthesizer")
        sys.exit(2)

    print("Loading trial_balance samples:")
    for company_dir in sorted(p for p in tb_root.iterdir() if p.is_dir()):
        print(f"  {load_trial_balance_company(company_dir)}")

    print("\nLoading tally_xml samples:")
    for company_dir in sorted(p for p in xml_root.iterdir() if p.is_dir()):
        print(f"  {load_tally_xml_company(company_dir)}")

    print("\nDone.")


if __name__ == "__main__":
    main()
