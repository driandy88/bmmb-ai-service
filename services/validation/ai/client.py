"""Vertex AI client construction kept outside validation orchestration."""

from collections.abc import Callable
from typing import Any

from ..infrastructure.settings import ValidationSettings


class VertexAIClientFactory:
    """Construct a Vertex client from validated runtime settings.

    ``client_constructor`` is injectable so callers and tests can provide a
    fake client without changing the orchestration code.
    """

    @staticmethod
    def create(
        settings: ValidationSettings,
        client_constructor: Callable[..., Any],
    ) -> Any:
        if not settings.gcp_project_id:
            raise ValueError("GCP_PROJECT_ID is required for Vertex AI review.")
        return client_constructor(
            vertexai=True,
            project=settings.gcp_project_id,
            location=settings.vertex_location,
        )
