"""
Tests for the write endpoints (POST/PUT/DELETE on /attributes and
/templates) added on top of the read-only Cloud SQL layer, plus the
X-Admin-Key gate in front of them. Runs against the REAL bmmb_dev database
(same as test_schema_builder.py) -- every test creates its own throwaway
rows (name-prefixed to avoid collisions) and cleans them up, rather than
touching the 15 seeded templates.
"""
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

ADMIN_KEY = os.environ["ADMIN_API_KEY"]
AUTH_HEADERS = {"X-Admin-Key": ADMIN_KEY}

# Unique per-session suffix for every throwaway row. These tests write to the
# shared bmmb_dev database, so a fixed name collides when two CI jobs run at
# once (the second 400s with "name already exists"), and a run that crashes
# mid-test leaves a fixed-named row that blocks every later run -- worse, once
# a __test_tmpl__ template wires the attribute, the attribute can't be deleted
# (409 in use) so the cleanup can't recover. A fresh suffix per session
# sidesteps both: names never clash across runs, and any crash orphan is
# harmlessly ignored rather than blocking.
_RUN = uuid4().hex[:8]


@pytest.fixture
def new_attribute():
    """Creates a throwaway attribute (uniquely named per run), yields its JSON
    body, deletes it after."""
    r = client.post(
        "/attributes/",
        json={"name": f"__test_attr__ Invoice Number {_RUN}", "description": "desc",
              "data_type": "Alphanumeric", "example": "INV-1"},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 201, r.text
    attr = r.json()
    yield attr
    client.delete(f"/attributes/{attr['id']}", headers=AUTH_HEADERS)


class TestAdminKeyGate:
    def test_post_attribute_without_key_401(self):
        r = client.post("/attributes/", json={"name": "x", "data_type": "Alphanumeric"})
        assert r.status_code == 401

    def test_post_attribute_with_wrong_key_401(self):
        r = client.post(
            "/attributes/",
            json={"name": "x", "data_type": "Alphanumeric"},
            headers={"X-Admin-Key": "not-the-real-key"},
        )
        assert r.status_code == 401

    def test_get_attributes_needs_no_key(self):
        r = client.get("/attributes/")
        assert r.status_code == 200

    def test_get_templates_needs_no_key(self):
        r = client.get("/templates/")
        assert r.status_code == 200

    def test_extract_endpoint_needs_no_key(self):
        # Missing template_id -> 422, not 401 -- confirms /extract was never gated.
        r = client.post("/extract", data={})
        assert r.status_code == 422


class TestAttributeCRUD:
    def test_create_and_get(self, new_attribute):
        r = client.get(f"/attributes/{new_attribute['id']}")
        assert r.status_code == 200
        assert r.json()["name"] == new_attribute["name"]
        assert r.json()["data_type"] == "Alphanumeric"

    def test_duplicate_name_400(self, new_attribute):
        r = client.post(
            "/attributes/",
            json={"name": new_attribute["name"], "data_type": "Alphanumeric"},
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 400

    def test_update(self, new_attribute):
        r = client.put(
            f"/attributes/{new_attribute['id']}",
            json={"description": "updated description"},
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 200
        assert r.json()["description"] == "updated description"
        assert r.json()["name"] == new_attribute["name"]  # untouched fields survive

    def test_delete(self):
        r = client.post(
            "/attributes/",
            json={"name": f"__test_attr__ to delete {_RUN}", "data_type": "Numeric"},
            headers=AUTH_HEADERS,
        )
        attr_id = r.json()["id"]
        r = client.delete(f"/attributes/{attr_id}", headers=AUTH_HEADERS)
        assert r.status_code == 204
        assert client.get(f"/attributes/{attr_id}").status_code == 404

    def test_delete_unknown_404(self):
        r = client.delete("/attributes/00000000-0000-0000-0000-000000000000", headers=AUTH_HEADERS)
        assert r.status_code == 404

    def test_delete_attribute_in_use_409(self):
        # "MISC Code" is wired to the seeded "Company Act Section 14"
        # template -- must refuse deletion, not silently orphan it.
        misc_code_id = next(a["id"] for a in client.get("/attributes/").json() if a["name"] == "MISC Code")
        r = client.delete(f"/attributes/{misc_code_id}", headers=AUTH_HEADERS)
        assert r.status_code == 409
        assert "Company Act Section 14" in r.json()["detail"]


class TestTemplateCRUD:
    def test_create_with_explicit_prompt(self, new_attribute):
        r = client.post(
            "/templates/",
            json={
                "name": f"__test_tmpl__ Invoice {_RUN}",
                "description": "A test invoice template",
                "group_name": "Test Group",
                "llm_prompt": "custom prompt text",
                "attributes": [{"attribute_id": new_attribute["id"], "frequency": "Unique"}],
            },
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["llm_prompt"] == "custom prompt text"
        assert len(body["template_attributes"]) == 1
        assert body["template_attributes"][0]["attribute"]["name"] == new_attribute["name"]
        client.delete(f"/templates/{body['id']}", headers=AUTH_HEADERS)

    def test_create_auto_generates_prompt_when_omitted(self, new_attribute):
        tmpl_name = f"__test_tmpl__ Auto Prompt {_RUN}"
        r = client.post(
            "/templates/",
            json={
                "name": tmpl_name,
                "attributes": [{"attribute_id": new_attribute["id"], "frequency": "Unique"}],
            },
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["llm_prompt"]
        assert new_attribute["name"] in body["llm_prompt"]
        assert tmpl_name in body["llm_prompt"]
        client.delete(f"/templates/{body['id']}", headers=AUTH_HEADERS)

    def test_create_without_key_401(self):
        r = client.post("/templates/", json={"name": "x", "attributes": []})
        assert r.status_code == 401

    def test_create_unknown_attribute_404(self):
        r = client.post(
            "/templates/",
            json={
                "name": f"__test_tmpl__ bad ref {_RUN}",
                "attributes": [{"attribute_id": "00000000-0000-0000-0000-000000000000", "frequency": "Unique"}],
            },
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 404

    def test_update_replaces_attributes(self, new_attribute):
        r = client.post(
            "/templates/",
            json={"name": f"__test_tmpl__ Update Me {_RUN}", "attributes": []},
            headers=AUTH_HEADERS,
        )
        template_id = r.json()["id"]

        r = client.put(
            f"/templates/{template_id}",
            json={"attributes": [{"attribute_id": new_attribute["id"], "frequency": "Multiple", "row_group": None}]},
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 200
        assert len(r.json()["template_attributes"]) == 1
        assert r.json()["template_attributes"][0]["frequency"] == "Multiple"

        client.delete(f"/templates/{template_id}", headers=AUTH_HEADERS)

    def test_update_partial_leaves_attributes_untouched(self, new_attribute):
        r = client.post(
            "/templates/",
            json={"name": f"__test_tmpl__ Partial {_RUN}", "attributes": [{"attribute_id": new_attribute["id"]}]},
            headers=AUTH_HEADERS,
        )
        template_id = r.json()["id"]

        r = client.put(
            f"/templates/{template_id}",
            json={"description": "new description only"},
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 200
        assert r.json()["description"] == "new description only"
        assert len(r.json()["template_attributes"]) == 1  # untouched

        client.delete(f"/templates/{template_id}", headers=AUTH_HEADERS)

    def test_delete(self):
        r = client.post("/templates/", json={"name": f"__test_tmpl__ to delete {_RUN}", "attributes": []}, headers=AUTH_HEADERS)
        template_id = r.json()["id"]
        r = client.delete(f"/templates/{template_id}", headers=AUTH_HEADERS)
        assert r.status_code == 204
        assert client.get(f"/templates/{template_id}").status_code == 404

    def test_delete_unknown_404(self):
        r = client.delete("/templates/00000000-0000-0000-0000-000000000000", headers=AUTH_HEADERS)
        assert r.status_code == 404
