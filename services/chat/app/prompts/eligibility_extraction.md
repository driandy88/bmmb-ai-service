<!--
purpose : Extract the five Tier-1 eligibility slots plus the informational
          operating_profit slot from the conversation, and phrase the verdict
          explanation AFTER rules.py has decided.
model   : gemini-2.5-flash (Vertex AI), structured output for extraction.
output  : JSON with the six slots (null when not stated) — see schema below.
excel   : Sheet 5 (Tier-1 upper table). Tier-2 topics are OUT — never extract or
          reason about them; the agent routes those to Sales.
notes   : You EXTRACT and EXPLAIN. You do NOT decide eligibility — rules.py does
          (brief §12). Never coach the customer on what values to give.
          operating_profit is collected but NEVER gates the verdict (policy:
          self-declared figures for it aren't trusted) — extract it the same as
          the others, rules.py simply never reads it.
-->

Extract the customer's self-declared business figures from the conversation
into the schema below. Use `null` for anything not clearly stated. Do not guess,
infer, or "helpfully" fill values the customer didn't give.

## Slots (all optional; null if unstated)
- `business_age_years` (number): how many years the business has operated.
- `total_equity_or_net_worth` (number, RM): total equity or net worth.
- `revenue` (number, RM): revenue / turnover / sales (annual).
- `operating_profit` (number, RM): operating profit. Can be negative.
- `end_balance` (number, RM): average bank end balance over the last 6 months.
- `staff_count` (integer): number of staff/employees.

## Rules
- Normalise units: "1 juta" / "1 million" / "RM1m" → 1000000. "5 orang" → 5.
- If the customer states a range, take a single representative number only if
  unambiguous; otherwise null.
- NEVER extract or discuss Tier-2 items (CTOS, CCRIS, DSCR, gearing,
  insolvency, connected party, AMLA). If the customer raises them, leave the
  slots as-is; the agent will hand off to Sales.

## Conversation
{history}

## Latest customer message
{message}

Return ONLY the JSON object with the six slot keys.
