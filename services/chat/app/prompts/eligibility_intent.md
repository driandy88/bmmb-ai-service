<!--
purpose : Only called on a FRESH eligibility turn (no Tier-1 slots collected
          yet). Decide whether the customer wants the full pre-eligibility
          check started, or is asking about one or more specific criteria
          without wanting to start the flow.
model   : gemini-2.5-flash (Vertex AI), structured output (temperature 0).
output  : JSON {action: "start_check"|"answer_criterion"|"continue", topics: [str,...]}
notes   : You DECIDE WHICH THING HAPPENS NEXT. You do NOT state a criterion's
          exact number, and you do NOT decide eligibility — rules.py does that,
          and the answer to "answer_criterion" is composed afterward from a
          fixed, qualitative phrase table, never from you. `topics`, when
          present, must be drawn only from the list below.
-->

The customer has raised something about SME financing eligibility, and nothing
has been collected yet this conversation. Decide what they actually want:

- **`start_check`** — they want to know if THEY qualify, or want the check
  started ("am I eligible?", "can I qualify?", "check my eligibility").
- **`answer_criterion`** — they're asking about one or more SPECIFIC criteria
  in the abstract, not asking to be assessed themselves ("what revenue do I
  need?", "how long does my business need to have been operating?", "is there
  a staff count requirement?"). List every criterion topic they touched on in
  `topics`.
- **`continue`** — the message doesn't clearly fit either (rare) — default to
  `start_check` if genuinely unsure; it's the safer fallback, it just opens the
  check rather than answering nothing.

## Criteria topics (use these exact keys in `topics`)
- `business_age_years` — how long the business has been operating
- `total_equity_or_net_worth` — total equity or net worth
- `revenue` — revenue / turnover / sales
- `operating_profit` — operating profit
- `end_balance` — average bank end balance
- `staff_count` — number of staff

## Examples
- "What revenue do I need to qualify?" → `{"action": "answer_criterion", "topics": ["revenue"]}`
- "Is there a minimum number of years?" → `{"action": "answer_criterion", "topics": ["business_age_years"]}`
- "Am I eligible for SME financing?" → `{"action": "start_check", "topics": []}`
- "Can you check if I qualify?" → `{"action": "start_check", "topics": []}`
- "What do you look at for revenue and staff count?" → `{"action": "answer_criterion", "topics": ["revenue", "staff_count"]}`

## Conversation
{history}

## Latest customer message
{message}

Return ONLY the JSON object.
