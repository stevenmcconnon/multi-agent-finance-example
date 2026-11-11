"""L5 authorization half: Authorizer adapters chosen by HR_AUTHZ_BACKEND, plus the audit door."""

from __future__ import annotations

from ..config import settings
from ..interfaces import Authorizer
from .audit import record
from .group_cache import GroupCache

__all__ = ["GroupCache", "get_authorizer", "record"]


def get_authorizer(backend: str | None = None) -> Authorizer:
    name = (backend or settings.hr_authz_backend).lower()
    if name == "cedar_local":
        from .cedar_local import CedarLocalAuthorizer

        return CedarLocalAuthorizer()
    if name == "verified_permissions":
        from .verified_permissions import VerifiedPermissionsAuthorizer

        return VerifiedPermissionsAuthorizer()
    raise ValueError(f"unknown HR_AUTHZ_BACKEND {name!r} (cedar_local | verified_permissions)")
