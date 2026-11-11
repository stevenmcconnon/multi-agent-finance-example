"""L3 Model gateway: the only place model vendors are spoken to. Chosen by HR_MODEL_BACKEND."""

from __future__ import annotations

from ..config import settings
from ..interfaces import ModelGateway
from .fake import ABSTAIN_TEXT, FakeGateway, extract_json, format_evidence

__all__ = ["ABSTAIN_TEXT", "FakeGateway", "extract_json", "format_evidence", "get_gateway"]


def get_gateway(backend: str | None = None) -> ModelGateway:
    name = (backend or settings.hr_model_backend).lower()
    if name == "fake":
        return FakeGateway()
    if name == "bedrock":
        from .bedrock import BedrockGateway

        return BedrockGateway()
    raise ValueError(f"unknown HR_MODEL_BACKEND {name!r} (fake | bedrock)")
