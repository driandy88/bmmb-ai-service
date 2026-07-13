"""
Tests for the write endpoints (POST/PUT/DELETE on /attributes and
/templates) added on top of the read-only Cloud SQL layer, plus the
X-Admin-Key gate in front of them. Runs against the REAL bmmb_dev database
(same as test_schema_builder.py) -- every test creates its own throwaway
rows (name-prefixed to avoid collisions) and cleans them up, rather than
touching the 15 seeded templates.
"""
import os

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

ADMIN_KEY = os.environ["ADMIN_API_KEY"]
AUTH_HEADERS = {"X-Admin-Key": ADMIN_KEY}


@pytest.fixture
def new_attribute():
    """Creates a throwaway attribute, yields its JSON body, deletes it after."""
    r = client.post(
        "/attributes/",
        json={"name": "__test_attr__ Invoice Number", "description": "desc", "data_type": "Alphanumeric", "example": "INV-1"},
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
        assert r.json()["name"] == "__test_attr__ Invoice Number"
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
            json={"name": "__test_attr__ to delete", "data_type": "Numeric"},
            headers=AUTH_HEADERS,
        )
        attr_id = r.json()["id"]
        r = client.delete(f"/attributes/{attr_id}", headers=AUTH_HEADERS)
        assert r.status_code == 204
        assert client.get(f"/attributes/{attr_id}").status_code == 404

    def test_delete_unknown_404(self):
        r = client.delete("/attributes/999999", headers=AUTH_HEADERS)
        assert r.status_code == 404

    def test_delete_attribute_in_use_409(self):
        # Attribute id 1 ("MISC Code") is wired to the seeded "Company Act
        # Section 14" template -- must refuse deletion, not silently orphan it.
        r = client.delete("/attributes/1", headers=AUTH_HEADERS)
        assert r.status_code == 409
        assert "Company Act Section 14" in r.json()["detail"]


class TestTemplateCRUD:
    def test_create_with_explicit_prompt(self, new_attribute):
        r = client.post(
            "/templates/",
            json={
                "name": "__test_tmpl__ Invoice",
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
        r = client.post(
            "/templates/",
            json={
                "name": "__test_tmpl__ Auto Prompt",
                "attributes": [{"attribute_id": new_attribute["id"], "frequency": "Unique"}],
            },
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["llm_prompt"]
        assert new_attribute["name"] in body["llm_prompt"]
        assert "__test_tmpl__ Auto Prompt" in body["llm_prompt"]
        client.delete(f"/templates/{body['id']}", headers=AUTH_HEADERS)

    def test_create_without_key_401(self):
        r = client.post("/templates/", json={"name": "x", "attributes": []})
        assert r.status_code == 401

    def test_create_unknown_attribute_404(self):
        r = client.post(
            "/templates/",
            json={"name": "__test_tmpl__ bad ref", "attributes": [{"attribute_id": 999999, "frequency": "Unique"}]},
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 404

    def test_update_replaces_attributes(self, new_attribute):
        r = client.post(
            "/templates/",
            json={"name": "__test_tmpl__ Update Me", "attributes": []},
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
            json={"name": "__test_tmpl__ Partial", "attributes": [{"attribute_id": new_attribute["id"]}]},
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
        r = client.post("/templates/", json={"name": "__test_tmpl__ to delete", "attributes": []}, headers=AUTH_HEADERS)
        template_id = r.json()["id"]
        r = client.delete(f"/templates/{template_id}", headers=AUTH_HEADERS)
        assert r.status_code == 204
        assert client.get(f"/templates/{template_id}").status_code == 404

    def test_delete_unknown_404(self):
        r = client.delete("/templates/999999", headers=AUTH_HEADERS)
        assert r.status_code == 404
