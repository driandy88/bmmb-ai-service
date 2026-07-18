from services.validation.ai.client import VertexAIClientFactory
from services.validation.infrastructure.settings import ValidationSettings


def test_settings_read_environment_at_call_time(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "project-a")
    monkeypatch.setenv("VERTEX_LOCATION", "asia-test1")
    monkeypatch.setenv("VALIDATION_AI_MODEL", "test-model")

    settings = ValidationSettings.from_env()

    assert settings.gcp_project_id == "project-a"
    assert settings.vertex_location == "asia-test1"
    assert settings.model_name == "test-model"


def test_vertex_client_factory_is_injectable():
    settings = ValidationSettings(
        gcp_project_id="project-a",
        vertex_location="asia-test1",
        model_name="test-model",
        max_ai_retries=2,
        ai_retry_backoff_seconds=1.0,
    )
    calls = []

    def fake_constructor(**kwargs):
        calls.append(kwargs)
        return "fake-client"

    client = VertexAIClientFactory.create(settings, fake_constructor)

    assert client == "fake-client"
    assert calls == [{
        "vertexai": True,
        "project": "project-a",
        "location": "asia-test1",
    }]
