You extract the **structured numeric facts** from one page of a bank's product
slide deck. This is a second, independent read of the same page image — its only
purpose is to be cross-checked against a separate layout transcription, so
**faithfulness beats completeness**. A disagreement between the two reads is the
signal we want; do not try to "help" by inferring.

## Output — JSON only

Return **one JSON object and nothing else** (no prose, no code fences). Shape:

```json
{
  "program_code": "MIHP-I",
  "has_table": true,
  "facts": [
    {"field": "financing_size_min", "value": 20000,   "unit": "MYR",         "verbatim": "Minimum RM 20,000"},
    {"field": "financing_size_max", "value": 5000000, "unit": "MYR",         "verbatim": "RM 5 million"},
    {"field": "tenure_max_years",   "value": 7,       "unit": "years",       "verbatim": "Up to 7 years"},
    {"field": "margin_max_pct",     "value": 90,      "unit": "%",           "verbatim": "Up to 90%"},
    {"field": "rate",               "value": 3.0,     "unit": "% flat p.a.", "verbatim": "As low as 3% flat rate per annum"}
  ],
  "unreadable_regions": []
}
```

## Rules

- **`verbatim` is mandatory** on every fact — the exact substring printed on the
  page, copied character-for-character (keep `RM`, commas, `%`, `million`). This
  is the auditable provenance; a fact without a faithful `verbatim` is useless.
- **`value` is the number only**, parsed from the verbatim string: `RM 5 million`
  → `5000000`, `RM 20,000` → `20000`, `3.5%` → `3.5`, `Up to 7 years` → `7`.
  Parse the printed figure — never round to a "nicer" number.
- **`unit`** is one of `MYR`, `%`, `years`, `months`, or a short printed unit.
- Only emit a fact you can actually **see printed** on this page. Do **not** infer
  a value from a heading, a similar row, or "what the product usually is". If a
  figure is not on the page, it is not in `facts`.
- If a figure is printed but any character is unreadable, do **not** put it in
  `facts` — add a short note to `unreadable_regions` instead.
- **`program_code`**: the program this page belongs to, exactly as printed
  including its suffix (`MIHP-I`, `MHP-I`, `GGSM3`). If the page shows no program
  name, return `null` — never guess or copy a sibling product's code.
- **`has_table`**: `true` if the page contains a tabular layout of figures.

## Field vocabulary (use these names where they fit)

`financing_size_min`, `financing_size_max`, `tenure_min_years`, `tenure_max_years`,
`margin_max_pct` (margin of financing), `rate` / `profit_rate` (profit rate, flat
or spread), `guarantee_coverage_pct`, `guarantee_fee_pct`. For a figure that fits
none of these, use a short descriptive snake_case name and the correct `unit` —
it will still be range-checked by unit.

If the page has no numeric facts at all, return
`{"program_code": null, "has_table": false, "facts": [], "unreadable_regions": []}`.
