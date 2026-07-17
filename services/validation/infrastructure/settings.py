"""Runtime configuration for the validation service."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class ValidationSettings:
    gcp_project_id: str | None
    vertex_location: str
    model_name: str
    max_ai_retries: int
    ai_retry_backoff_seconds: float

    @classmethod
    def from_env(cls) -> "ValidationSettings":
        """Read current environment values after module-level dotenv loading."""
        return cls(
            gcp_project_id=os.environ.get("GCP_PROJECT_ID"),
            vertex_location=os.environ.get("VERTEX_LOCATION", "asia-southeast1"),
            model_name=os.environ.get("VALIDATION_AI_MODEL", "gemini-2.5-flash"),
            max_ai_retries=int(os.environ.get("VALIDATION_AI_MAX_RETRIES", "2")),
            ai_retry_backoff_seconds=float(
                os.environ.get("VALIDATION_AI_RETRY_BACKOFF_SECONDS", "1.0")
            ),
        )
