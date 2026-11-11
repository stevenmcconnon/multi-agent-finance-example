"""L3 Model gateway: the only place model vendors are spoken to. Chosen by HR_MODEL_BACKEND."""

from __future__ import annotations

from contextvars import ContextVar

from ..config import settings
from ..interfaces import ModelCallFailed, ModelGateway, ModelRefused

# No silent stubs: when a gateway answers `understand` or `plan` from the deterministic fallback after a model
# error it leaves a note here, and the graph node that made the call puts it in its status event. A ContextVar,
# because one gateway instance serves concurrent turns and each graph node runs in its own task context.
_notes: ContextVar[tuple[str, ...]] = ContextVar("gateway_notes", default=())


def note(message: str) -> None:
    _notes.set((*_notes.get(), message))


def take_notes() -> list[str]:
    notes = list(_notes.get())
    _notes.set(())
    return notes


from .fake import ABSTAIN_TEXT, FakeGateway, extract_json, format_evidence  # noqa: E402  (after note: bedrock imports it)

__all__ = [
    "ABSTAIN_TEXT",
    "FakeGateway",
    "ModelCallFailed",
    "ModelRefused",
    "extract_json",
    "format_evidence",
    "get_gateway",
    "note",
    "take_notes",
]


def get_gateway(backend: str | None = None) -> ModelGateway:
    name = (backend or settings.hr_model_backend).lower()
    if name == "fake":
        return FakeGateway()
    if name == "bedrock":
        from .bedrock import BedrockGateway

        return BedrockGateway()
    raise ValueError(f"unknown HR_MODEL_BACKEND {name!r} (fake | bedrock)")
