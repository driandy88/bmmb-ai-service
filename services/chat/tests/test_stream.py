"""POST /chat/stream — SSE framing + event sequence."""
import json

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _collect(payload):
    events, reply, done = [], "", None
    with client.stream("POST", "/chat/stream", json=payload) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        cur = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                cur = line.split(":", 1)[1].strip(); events.append(cur)
            elif line.startswith("data:"):
                d = json.loads(line.split(":", 1)[1].strip())
                if cur == "token":
                    reply += d["text"]
                elif cur == "done":
                    done = d
    return events, reply, done


def test_stream_emits_start_tokens_done():
    events, reply, done = _collect({"message": "What SME financing programs do you offer?"})
    assert events[0] == "start"
    assert events[-1] == "done"
    assert events.count("token") > 0
    assert reply.strip()                              # reply reassembles from tokens
    assert set(done) == {"session_id", "reply", "intent", "ui_action", "citations",
                         "handoff", "state", "audit"}
    assert done["reply"] == reply                    # streamed text == envelope reply


def test_stream_carries_structured_fields():
    # An eligibility verdict must still deliver its buttons on the `done` event.
    full = {"business_age_years": 5, "total_equity_or_net_worth": 500_000, "revenue": 2_000_000,
            "working_capital_limit": 300_000, "end_balance": 100_000, "staff_count": 8}
    _, _, done = _collect({"message": "am I eligible?",
                           "context": {"state": {"stage": "eligibility_slotfill", "collected_slots": full}}})
    assert done["ui_action"]["type"] == "show_eligibility_result"
    assert done["ui_action"]["payload"]["outcome"] == "PASS"
