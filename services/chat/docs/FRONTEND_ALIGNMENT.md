# Frontend ↔ Agent Alignment

**Purpose.** `ChatWidget.jsx` is a thin, server-driven renderer: it holds no flow
logic, renders whatever `step` + `ui` the backend returns, and posts back an
`action` + `payload`. Its own comment names the swap-in point for a real agent:

> *"Replacing `portalAssistant.js` behind `routes/portal.js` (or pointing
> `sendMessage` at a different endpoint) is the only change a real agent
> integration needs."*

This document is the contract for that swap: what our `/chat` service covers,
what it deliberately does **not**, and the exact field-by-field mapping an
adapter needs to sit between the widget's `{ messages, step, ui, appId }`
protocol and our `ChatResponse` envelope.

---

## 1. The seam

```
ChatWidget.jsx  ──startSession / sendMessage(action,payload)──►  api/portal.js
                                                                      │
                                                              routes/portal.js  ◄── the ADAPTER lives here
                                                                   /      \
                                                   reasoning turns/        \ stateful enrolment turns
                                                                 ▼          ▼
                                                   /chat (our agent)   portalAssistant.js + app store
                                                   STATELESS brain     stateful journey owner
```

The adapter is a **dispatcher + translator**. It routes each inbound turn to the
right owner, and translates our envelope back into the widget's step protocol.

---

## 2. Two brains, one journey — ownership split

Our chat service is **stateless by design** (server persists nothing; the
client's `context` is the only memory). The widget's journey is **stateful**
(`appId` lifecycle: create lead, save draft, track, resume, OTP). These are two
different jobs. They do not conflict — they **split**:

| Owner | Responsibility | Journey steps |
|---|---|---|
| **Agent (`/chat`)** — stateless NLU / decision brain | understand, guard, recommend, indicative-eligibility, route/handoff | `freeText`, `discover`, `qualify`, `products`, `eligResult`, `salesContact` |
| **`portalAssistant.js` + app store** — stateful journey owner | forms, `appId` lifecycle, drafts, tracking, OTP, consent, document upload/validation, UI config | `menu`, `contact`, `manual`, `consent`, `track`, `trackOtp`, `tracked`, `upload`, `fullUpload`, `preValidationFailed`, `done` |

Rule of thumb: **if a step needs to persist or look up application state, it is
NOT the agent's** — the agent only reasons about the current turn.

---

## 3. Inbound — frontend `action` → our `ChatRequest`

The widget always sends `sendMessage({ sessionId, action, payload })`. Our
request is:

```jsonc
{ "session_id": "...", "message": "...", "channel": "customer|branch",
  "application_id": "...|null",
  "context": { "history": [...], "state": { "stage": "...", "collected_slots": {...}, "last_intent": "..." } } }
```

Note the two fields that already line up: **`channel` = the widget's `mode`**,
and **`application_id` = its `appId`** (we accept it as context; we never mint
one — see §5).

| Inbound action | Goes to | How the adapter maps it |
|---|---|---|
| `freeText {text}` | **agent** | `message = text` (stage carried from context) |
| `purpose:pick {purpose}` | **agent** | `message = purpose`, `state.stage = "funnel_purpose"` |
| `qualify:pick {band}` | **agent** | `message = band`, `state.stage = "funnel_amount"` |
| `apply:unsure` | **agent** | `message = "help me choose"` → program funnel (purpose) |
| `product:pick {name}` | **agent → boundary** | `message = "I'd like <name>"`; on dispatch to *initiate*, hand to portalAssistant |
| menu keys (`menu`/`bizChoice`/`productActions` `o.key`) | **portalAssistant** | scripted navigation; a few (e.g. "check eligibility") seed an agent turn |
| `contact:submit`, `sales:submit` | **portalAssistant** | form capture; `sales:submit` may also fire our handoff notification |
| `manual:submit`, `biz:upload`, `biz:manual` | **portalAssistant** | business-details forms |
| `consent:submit`, `consent:save-draft` | **portalAssistant** | consent + draft persistence |
| `track:lookup`, `track:otp`, `track:again`, `track:details`, `track:continue` | **portalAssistant** | lookup + OTP + resume (needs app store) |
| `upload:continue`, `prevalidation:retry` | **portalAssistant** | document checklist + validation service |
| `restart` | **both** | new `session_id` on the agent; reset journey on portalAssistant |

---

## 4. Outbound — our `ChatResponse` → widget `{ messages, step, ui, appId }`

Our envelope:

```jsonc
{ "session_id", "reply", "intent": {primary,confidence,secondary},
  "ui_action": {type,payload}, "citations": [], "handoff": {required,reason,contact},
  "state": {stage,collected_slots,last_intent}, "audit": {route,rule_version,...} }
```

Fixed rules:

- **`messages`** ← `[{ isBot: true, text: reply }]`. Our `reply` is a single
  composed string (secondary intents are already folded into it server-side).
- **`appId`** ← unchanged. The agent never returns one; carry portal's existing
  `appId` (§5).
- **`step` + `ui`** ← derived from `ui_action.type` (+ `payload`), `handoff`,
  and `state.stage`, per the table below.

| Our signal (`ui_action.type` / payload / state) | → `step` | → `ui` |
|---|---|---|
| `show_program_options`, `payload.step="purpose"` | `discover` | `{ title, purposeOptions: payload.options }` |
| `show_program_options`, `payload.step="amount"` | `qualify` | `{ title, sub, options: payload.bands }` |
| `show_program_options`, `payload.step="result"` | `products` | `{ products: payload.products, canUnsure: true }` |
| `render_eligibility_form` (slot-fill) | *(omit `ui`)* | slot question is in `reply`; composer stays — customer types the value |
| `show_eligibility_result` (verdict) | `eligResult` | `{ outcome: payload.outcome, options: payload.options }` — green check when `outcome==="PASS"`; each option `{key,label}` → `sendTurn(key)` |
| `show_contact_card` (handoff) | `salesContact` | `{ title, hasContact: true, contact: payload }` |
| `open_application_link`, no `mode` (initiate) | **boundary** → portalAssistant begins application (`consent`/`manual`) | pass `payload.url` |
| `open_application_link`, `mode="continue"` (lookup) | **boundary** → portalAssistant resume (`tracked`, `canContinue`) | pass `url`, `stage` |
| `open_application_link`, `mode="track"` (lookup) | **boundary** → portalAssistant tracking (`tracked`) | pass `url`, `stage` |
| `ui_action=none`, `state.stage="await_application_id_*"` | *(omit `ui`)* | agent asks for the application ID as free text; composer stays |
| REFUSE / CLARIFY / CANNED (all `ui_action=none`) | *(omit `ui`)* | just a bot message; the current widget stays in place (the widget already treats an omitted `ui` as "leave widget as-is") |

**Disambiguating `ui_action=none`.** A few outcomes still share `type: none`
(refusal, clarify, OOS redirect, await-app-id). The adapter reads
**`audit.action`** (`refuse|clarify|canned|dispatch|handoff` — see §6.2, now
shipped) to tell them apart deterministically, falling back to `state.stage`
(`await_application_id_*` → ask for ID) where needed.

---

## 5. Why `appId` / persistence is not ours

The agent accepts `application_id` inbound (so eligibility continuation and
lookups can reference it) but **never creates, stores, or returns one**. Lead
creation, drafts, resume, and tracking all require server-side application state,
which the stateless chat service intentionally does not hold. That entire
lifecycle — and the `onLeadCreated(appId)` the widget expects — stays with
`portalAssistant.js` / the application store.

Consequence: the widget's `appId` is always minted and owned by portalAssistant.
The agent is a pure function of `(message, context)`; give it the same inputs
twice and it returns the same envelope.

---

## 6. Gaps to close on the agent side (small, concrete)

These are the deltas that make the agent fully serve its half of the journey:

1. **Accept structured picks as stage-hinted turns.** The reasoning picks
   (`purpose:pick`, `qualify:pick`, `apply:unsure`, `product:pick`) map cleanly
   onto `message` + `state.stage` today (§3) — but document this in the OpenAPI
   so the adapter isn't guessing. *(No code change; contract note.)*
2. **~~Expose the routing `action` in the envelope.~~ ✅ DONE.** `audit.action ∈
   {refuse, clarify, handoff, dispatch, canned}` is now in every envelope
   (computed by `routing.decide`, surfaced in `_assemble`). This removes the
   `ui_action=none` ambiguity in §4 — the adapter reads `audit.action` directly.
3. **~~`eligResult` verdict → `options`.~~ ✅ DONE.** The verdict now emits
   `ui_action.type="show_eligibility_result"` with `{outcome: PASS|FAIL, options:
   [{key,label}]}`, so the widget renders the PASS/FAIL menu directly. Button
   labels/keys are config (`eligibility_rules.yaml → result_actions`); the adapter
   maps each `key` (`apply:start`, `handoff:sales`, `guidelines:requirements`).
4. **Branch-mode wording.** `channel="branch"` is accepted but barely changes
   wording. If branch officers need different phrasing ("your customer" vs
   "you"), thread `channel` into the compose prompts.

None of these require the agent to become stateful.

---

## 7. What stays out (and stays correct)

- **Document upload / validation** (`upload`, `fullUpload`, `preValidationFailed`,
  `requiredDocKeys`): the full checklist + validation is owned by portalAssistant
  + the extraction & validation services. **But** the *document-driven
  eligibility* slice is now built on the agent: **`POST /chat/documents`**
  (multipart) calls the extraction service, maps 5 of the 6 Tier-1 slots out of
  the result (`agents/eligibility/document_map.py`, config
  `document_slot_map.yaml`), and continues the same eligibility flow. It reads
  **only** Tier-1 figures — Tier-2 fields (EBITDA/PBT/advances-to-director) in the
  same document are ignored. `staff_count` is in no template, so it stays typed.
  Backend switch `EXTRACTION_BACKEND=stub|http`; the real PDF round-trip needs the
  extraction service running + a real response to confirm the exact array-key
  names (they live in Cloud SQL, so `document_slot_map.yaml` is the one-line
  adjustment point).
- **Tracking + OTP** (`track*`): needs the app store; the agent only classifies
  the intent (`INS-07`) that gets the user there.
- **Consent, contact, manual-business forms**: structured capture, no NLU.
- **`getConfig`** (`contactRoles`, `businessTypes`, `businessTypeLabels`,
  `regions`): served by `config.js`. The agent has regions internally for
  handoff routing but does not serve UI config.

---

## 8. Coverage at a glance

| Journey step | Owner | Status |
|---|---|---|
| `freeText` | agent | ✅ core |
| `discover` / `qualify` / `products` | agent | 🟡 logic done; needs step/ui mapping (§4) |
| `eligResult` | agent | 🟡 verdict done; needs `options` payload (§6.3) |
| `salesContact` | agent | 🟡 produces contact card; form-submit is portalAssistant |
| `menu` / `bizChoice` / `productActions` | portalAssistant | scripted nav |
| `contact` / `manual` / `consent` | portalAssistant | ❌ not agent scope |
| `track` / `trackOtp` / `tracked` | portalAssistant | ❌ agent classifies intent only |
| `upload` / `fullUpload` / `preValidationFailed` | portalAssistant + extraction/validation | ❌ not agent scope |
| `done` + `appId` lifecycle | portalAssistant | ❌ stateless boundary (§5) |

**Aligned on the front half of the funnel** (understand → recommend →
indicative-eligibility → handoff). **The stateful back half stays with
portalAssistant** — by design, not omission.
