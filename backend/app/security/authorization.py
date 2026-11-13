"""Role-based authorization helpers."""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


ROLE_HIERARCHY: dict[str, int] = {
    Role.VIEWER.value: 1,
    Role.OPERATOR.value: 2,
    Role.ADMIN.value: 3,
}


def has_required_role(user_role: str, required_role: str) -> bool:
    return ROLE_HIERARCHY.get(user_role, 0) >= ROLE_HIERARCHY.get(required_role, 999)
