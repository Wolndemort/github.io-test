from dataclasses import dataclass


@dataclass(frozen=True)
class AuthContext:
    """Canonical actor context produced by an authentication provider."""

    user_id: int
    club_id: int
    actor_type: str
    role: str
    permissions: frozenset[str]
    auth_source: str
