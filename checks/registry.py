"""Registry of every check and the documents it requires.

coordinator.py uses this to determine which checks can run for a given
client/FY/version. Add a new CheckDefinition entry here whenever a new check
is built -- this is the one place that needs to know about every check that
exists.
"""
from dataclasses import dataclass
from typing import List

from checks.opening_balance_vs_prior_year_closing import (
    CHECK_ID as OPENING_BALANCE_CHECK_ID,
    CHECK_NAME as OPENING_BALANCE_CHECK_NAME,
    DATA_REQUIREMENTS as OPENING_BALANCE_REQUIREMENTS,
)
from checks.suspense_account_scrutiny import (
    CHECK_ID as SUSPENSE_ACCOUNT_CHECK_ID,
    CHECK_NAME as SUSPENSE_ACCOUNT_CHECK_NAME,
    DATA_REQUIREMENTS as SUSPENSE_ACCOUNT_REQUIREMENTS,
)
from checks.requirements import DataRequirement


@dataclass(frozen=True)
class CheckDefinition:
    check_id: str
    name: str
    requirements: List[DataRequirement]


CHECK_REGISTRY: List[CheckDefinition] = [
    CheckDefinition(
        check_id=OPENING_BALANCE_CHECK_ID,
        name=OPENING_BALANCE_CHECK_NAME,
        requirements=OPENING_BALANCE_REQUIREMENTS,
    ),
    CheckDefinition(
        check_id=SUSPENSE_ACCOUNT_CHECK_ID,
        name=SUSPENSE_ACCOUNT_CHECK_NAME,
        requirements=SUSPENSE_ACCOUNT_REQUIREMENTS,
    ),
]
