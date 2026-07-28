Here's a consolidated ticket list, grouped by root cause rather than by row (several scenarios are symptoms of the same underlying bug):

## Possible Extraction/Adapter bugs

**TICKET-1: Bank statement month-count is unreliable / systematically undercounts**
This is the highest-impact bug — it's cascading into false FAILED results across nearly every bank-statement validation rule, making the rules look broken when the real issue is upstream extraction.
- Affected: L0-SDN, L0-SOLE (10 of 12 extracted), L1-bankbank-format (3 of 6), L1-bankbank-mixed (5 of 6), L1-bankcont-adjacent (4 of 6), L1-bankcont-gap (2 of 6), L1-bankcont-overlap (2 of 6), L3-mixed-upload (**0 of 6** — total failure), L3-multifile (3 of 6)
- Suspected cause: possible file-count/temperature limits noted on L0-SDN; multi-file/mixed-shape uploads seem worst affected (L3 cases)
- Action: investigate ingestion pipeline for max-file limits, per-document parsing reliability, and consolidation logic when statements arrive as separate files vs. combined docs

**TICKET-2: CIF required fields return `null` instead of "Not Available"**
- Affected: L0-SDN, L1-bankbank-format
- Fix the field-mapping/default logic so missing CIF data is explicitly flagged as "Not Available" rather than `null` (null risks being misread as "not required" downstream)

**TICKET-3: Bank name normalization needs verification**
- Affected: L1-bankbank-format ("Maybank" vs "Malayan Banking Berhad" vs "Maybank Berhad")
- Overlaps with Ticket 1 (undercount) but specifically check whether differing bank name renderings are causing statements to be treated as separate/unmatched groups during extraction

## Validation logic bugs

**TICKET-4: Currency check not rejecting non-MYR statements**
- Affected: L1-bankcur-sgd
- Expected FAILED, got PASSED. SGD statements are passing the `accepted_bank_currency = MYR` check — likely a hard blocker fix

**TICKET-5: IC front/back "unreadable" (None) not triggering NEEDS_REVIEW**
- Affected: L2-icfront-none
- Expected NEEDS_REVIEW, got PASSED. Tri-state handling (True/False/None) for `ic_front_image_present` appears to be collapsing None into a pass state

**TICKET-6: Passport-based identity documents mishandled**
- Affected: L3-passport
- System reports "Missing 1 IC" when a director holds a passport instead of a Malaysian IC. Need explicit logic (and design decision) for how front/back and number-match rules apply to passports

**TICKET-7: Entity-type spelling variants and resolved bank-statement minimum**
- Affected: L1-bankdur-entity-spellings
- Confirm unrecognized entity_type strings (typos, alternate spellings, Malay terms) correctly resolve to the correct minimum (12mo for sole prop) rather than silently falling back to the lenient 6mo default — this is a policy-bypass risk, worth prioritizing

**TICKET-8: Entity name mismatch — confirm correct fail routing**
- Affected: L1-name-real
- Note says the fail "happened through the next step" — needs a QA pass to confirm the mismatch is caught at the right rule/stage, not accidentally slipping through and being caught elsewhere

## Policy decisions needed (blocking test completion, not bugs)

**TICKET-9: Sole Prop / Borang B rules**
- L1-borangb-consecutive: does a sole prop need 2 years of Borang B filings, or is 1 sufficient?
- L1-borangb-date-source: lock in that assessment year (not basis-period-end or filing date) drives freshness
- L1-borangb-seasonal-nov: decide whether Borang B gets its own freshness threshold, an exemption from the 18mo rule, or accepts seasonal rejections (a compliant sole prop currently fails part of every year)

---