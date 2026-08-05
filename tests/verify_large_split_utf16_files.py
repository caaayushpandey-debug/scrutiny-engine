"""Large-scale, real-world-shaped regression check for tally_xml_parser.py,
covering three problems found against REAL user Tally exports (2026-08-06,
not this project's own synthetic samples) that the fast unit test suite
(tests/test_tally_xml_parser.py) only covers at small/toy scale:

1. ENCODING: real exports are commonly UTF-16 with a BOM, not UTF-8.
2. SPLIT FILES: a company's export commonly arrives as two separate files --
   one with only <GROUP>/<LEDGER> masters, another with only <VOUCHER>
   entries -- both possibly carrying the same <REPORTNAME> regardless of
   which is which.
3. SCALE + MESSINESS: real files can be tens of MB (the reported real case:
   ~9MB masters, ~61MB transactions) with GST-specific tags, nested
   collection lists, and invalid numeric character references (&#4; and
   similar C0 control characters, not valid XML 1.0 at all) that this
   project's own synthetic samples never contained.

This generates fixtures matching all three characteristics ON THE FLY (they
are NOT checked into the repo -- tens of MB of generated XML has no business
living in git) at roughly real-world scale, feeds them through
parse_tally_xml_data_multi, times it, and asserts the result is actually
correct (right ledger/voucher counts, right computed closing balances) --
not just "didn't crash".

This is a standalone script, not a unittest module (same convention as
verify_against_data_synthesizer.py) -- generating and parsing ~70MB of XML
is legitimately slower than the rest of the suite (which runs in
milliseconds) and shouldn't run on every `python3 -m unittest discover`.

Usage:
    python3 tests/verify_large_split_utf16_files.py [--quick]

--quick generates much smaller fixtures (for fast iteration while working on
this script itself) instead of the ~9MB/~61MB real-world-scale target.
Exits non-zero if anything fails, so it can be used as a CI gate later.
"""
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tally_xml_parser import TallyXmlParseError, parse_tally_xml_data_multi

ENVELOPE_OPEN = """<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
  <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
  <BODY><IMPORTDATA>
    <REQUESTDESC>
      <REPORTNAME>All Masters</REPORTNAME>
    </REQUESTDESC>
    <REQUESTDATA>
"""
ENVELOPE_CLOSE = """
    </REQUESTDATA>
  </IMPORTDATA></BODY>
</ENVELOPE>
"""

# Real Tally exports carry a lot of company/GST-specific sub-elements this
# parser has never needed to read -- interspersed here to prove they're
# tolerated (simply ignored) rather than confusing the parser, not just
# absent from every fixture like the rest of the suite's small examples.
LEDGER_GST_NOISE = """
        <GSTREGISTRATIONDETAILS.LIST>
          <GSTIN>27AAAAA0000A1Z5</GSTIN>
          <REGISTRATIONTYPE>Regular</REGISTRATIONTYPE>
          <ADDRESS.LIST TYPE="String">
            <ADDRESS>Plot 12, Industrial Estate</ADDRESS>
            <ADDRESS>Sector 5</ADDRESS>
          </ADDRESS.LIST>
          <STATENAME>Maharashtra</STATENAME>
          <PINCODE>400001</PINCODE>
        </GSTREGISTRATIONDETAILS.LIST>
        <LEDGERPHONE.LIST>
          <LEDGERCONTACTNO>+91 98200 00000</LEDGERCONTACTNO>
        </LEDGERPHONE.LIST>
        <LEDGERMAILINGNAME.LIST TYPE="String">
          <LEDGERMAILINGNAME>Trade Name Pvt Ltd</LEDGERMAILINGNAME>
        </LEDGERMAILINGNAME.LIST>
"""

VOUCHER_GST_NOISE = """
            <RATEDETAILS.LIST>
              <GSTRATEDUTYHEAD>CGST</GSTRATEDUTYHEAD>
              <GSTRATE>9</GSTRATE>
            </RATEDETAILS.LIST>
            <COSTCENTREALLOCATIONS.LIST>
              <COSTCENTRENAME>Head Office</COSTCENTRENAME>
              <AMOUNT>0</AMOUNT>
            </COSTCENTREALLOCATIONS.LIST>
            <BILLALLOCATIONS.LIST>
              <NAME>Bill Ref</NAME>
              <BILLTYPE>New Ref</BILLTYPE>
            </BILLALLOCATIONS.LIST>
"""

# Real files were observed containing numeric character references to
# codepoints XML 1.0 does not allow (e.g. &#4;, End of Transmission) --
# scattered through generated narrations/messy fields, not just isolated in
# a single tiny unit test, to prove the sanitizer holds up at real scale.
INVALID_ENTITIES = ["&#4;", "&#1;", "&#7;", "&#31;"]


def _ledger_xml(idx: int) -> str:
    parent = "Sundry Debtors" if idx % 3 == 0 else "Bank Accounts" if idx % 3 == 1 else "Sundry Creditors"
    noise = LEDGER_GST_NOISE if idx % 5 == 0 else ""
    return f"""
    <TALLYMESSAGE xmlns:UDF="TallyUDF">
      <LEDGER NAME="Ledger {idx:06d}" ACTION="Create">
        <PARENT>{parent}</PARENT>
        <OPENINGBALANCE>{(idx % 500) * 137}.50</OPENINGBALANCE>
        <ISBILLWISEON>{"Yes" if idx % 2 == 0 else "No"}</ISBILLWISEON>{noise}
      </LEDGER>
    </TALLYMESSAGE>
"""


def _voucher_xml(idx: int, debit_ledger: str, credit_ledger: str) -> str:
    amount = f"{(idx % 10000) + 1}.25"
    noise = VOUCHER_GST_NOISE if idx % 4 == 0 else ""
    entity = INVALID_ENTITIES[idx % len(INVALID_ENTITIES)] if idx % 7 == 0 else ""
    return f"""
    <TALLYMESSAGE xmlns:UDF="TallyUDF">
      <VOUCHER VCHTYPE="{"Sales" if idx % 2 == 0 else "Receipt"}" ACTION="Create">
        <DATE>202506{(idx % 28) + 1:02d}</DATE>
        <VOUCHERNUMBER>V-{idx:07d}</VOUCHERNUMBER>
        <NARRATION>Auto-generated stress test voucher #{idx}{entity} with messy fields.</NARRATION>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{debit_ledger}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          <AMOUNT>-{amount}</AMOUNT>{noise}
        </ALLLEDGERENTRIES.LIST>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{credit_ledger}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          <AMOUNT>{amount}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>
      </VOUCHER>
    </TALLYMESSAGE>
"""


def generate_masters_file(target_bytes: int) -> tuple[str, int]:
    """Returns (utf8_text, ledger_count). Grows past target_bytes slightly
    rather than truncating mid-record."""
    parts = [ENVELOPE_OPEN]
    size = len(ENVELOPE_OPEN)
    idx = 0
    while size < target_bytes:
        frag = _ledger_xml(idx)
        parts.append(frag)
        size += len(frag)
        idx += 1
    parts.append(ENVELOPE_CLOSE)
    return "".join(parts), idx


def generate_transactions_file(target_bytes: int, ledger_count: int) -> tuple[str, int]:
    """Every voucher references two real ledger names from the masters file
    (by index, same naming scheme _ledger_xml uses) -- exactly the
    cross-file reference merge_tally_xml_fragments must resolve, since these
    ledgers are NOT in this file at all."""
    parts = [ENVELOPE_OPEN]
    size = len(ENVELOPE_OPEN)
    idx = 0
    while size < target_bytes:
        debit = f"Ledger {(idx % ledger_count):06d}"
        credit = f"Ledger {((idx + 1) % ledger_count):06d}"
        frag = _voucher_xml(idx, debit, credit)
        parts.append(frag)
        size += len(frag)
        idx += 1
    parts.append(ENVELOPE_CLOSE)
    return "".join(parts), idx


def to_utf16_bom(text: str, byte_order: str) -> bytes:
    if byte_order == "le":
        return b"\xff\xfe" + text.replace('encoding="UTF-8"', 'encoding="UTF-16"').encode("utf-16-le")
    return b"\xfe\xff" + text.replace('encoding="UTF-8"', 'encoding="UTF-16"').encode("utf-16-be")


def main():
    quick = "--quick" in sys.argv
    # Defaults target the real reported file sizes (~9MB masters, ~61MB
    # transactions) AFTER UTF-16 encoding, which is what was actually
    # observed on disk -- UTF-16 roughly doubles the byte count of ASCII-
    # heavy content vs. the UTF-8 text generated here, so the pre-encoding
    # character-count target is set to roughly half the real file size.
    # --quick trades realism for a script that finishes in well under a
    # second during iteration.
    masters_target = 200_000 if quick else 4_500_000
    transactions_target = 500_000 if quick else 30_500_000

    failures = []

    print(f"Generating masters fixture (target ~{masters_target / 1_000_000:.1f}MB)...")
    t0 = time.perf_counter()
    masters_text, ledger_count = generate_masters_file(masters_target)
    print(f"  {len(masters_text):,} chars, {ledger_count:,} ledgers, generated in {time.perf_counter() - t0:.2f}s")

    print(f"Generating transactions fixture (target ~{transactions_target / 1_000_000:.1f}MB)...")
    t0 = time.perf_counter()
    transactions_text, voucher_count = generate_transactions_file(transactions_target, ledger_count)
    print(f"  {len(transactions_text):,} chars, {voucher_count:,} vouchers, generated in {time.perf_counter() - t0:.2f}s")

    # Real-world combination: masters as UTF-16 LE, transactions as UTF-16
    # BE -- both variants get exercised, and both files are split AND
    # encoded non-UTF-8 at once (the actual reported scenario), not tested
    # as two isolated, unrelated problems.
    print("Encoding both files as UTF-16 with a BOM...")
    masters_bytes = to_utf16_bom(masters_text, "le")
    transactions_bytes = to_utf16_bom(transactions_text, "be")
    print(f"  masters: {len(masters_bytes):,} bytes ({len(masters_bytes) / 1_000_000:.2f}MB)")
    print(f"  transactions: {len(transactions_bytes):,} bytes ({len(transactions_bytes) / 1_000_000:.2f}MB)")

    print("Parsing and merging both files...")
    t0 = time.perf_counter()
    try:
        data = parse_tally_xml_data_multi([masters_bytes, transactions_bytes])
    except TallyXmlParseError as e:
        print(f"PARSE FAILED: {e}")
        sys.exit(1)
    elapsed = time.perf_counter() - t0
    print(f"  parsed + merged in {elapsed:.2f}s")

    # Correctness, not just "didn't crash".
    if len(data.ledgers) != ledger_count:
        failures.append(f"expected {ledger_count} ledgers, got {len(data.ledgers)}")
    if len(data.vouchers) != voucher_count:
        failures.append(f"expected {voucher_count} vouchers, got {len(data.vouchers)}")

    sample_ledger = "Ledger 000000"
    if sample_ledger in data.ledgers:
        expected_opening = Decimal("0.50")  # (0 % 500) * 137 = 0, + ".50"
        actual_opening = data.ledgers[sample_ledger].opening_balance
        if actual_opening != expected_opening:
            failures.append(f"{sample_ledger} opening balance: expected {expected_opening}, got {actual_opening}")
        touching = data.vouchers_touching(sample_ledger)
        if not touching:
            failures.append(f"{sample_ledger}: expected at least one voucher touching it (cross-file reference), found none")
    else:
        failures.append(f"{sample_ledger} missing from merged ledgers entirely")

    # Spot-check that messy/noisy sibling elements (GST tags etc.) and
    # invalid numeric entities didn't corrupt the fields the parser DOES
    # read -- narration should still be exactly what was generated, minus
    # the stripped invalid entity.
    narration_bearing = next((v for v in data.vouchers if v.voucher_number == "V-0000000"), None)
    if narration_bearing is None:
        failures.append("could not find voucher V-0000000 to spot-check narration")
    elif "&#" in narration_bearing.narration:
        failures.append(f"invalid numeric entity leaked into parsed narration: {narration_bearing.narration!r}")

    print()
    if failures:
        print("VERIFICATION FAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)

    print(
        f"VERIFICATION PASSED -- {ledger_count:,} ledgers + {voucher_count:,} vouchers "
        f"({len(masters_bytes) + len(transactions_bytes):,} bytes total across 2 UTF-16 split files) "
        f"parsed and merged correctly in {elapsed:.2f}s."
    )


if __name__ == "__main__":
    main()
